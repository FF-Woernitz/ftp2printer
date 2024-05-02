import argparse
import configparser
import logging
import platform
import re
import signal
import subprocess
import threading
import time
from ftplib import FTP
from os import path

import nextcloud_client

REGEX_FAX_NUMBER = r"[\d\.\_]*_Telefax\.(\d*)\.pdf"

LOG_FORMAT = "%(asctime)s %(levelname)s: %(message)s"
logging.basicConfig(format=LOG_FORMAT)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

parser = argparse.ArgumentParser(
    description='Print and Upload FAX',
    epilog='by Feuerwehr Wörnitz | TobsA')
parser.add_argument('configPath', help="Config file path")
parser.add_argument('-v', '--verbose', action='store_true')
args = parser.parse_args()

if args.verbose:
    logger.setLevel(logging.DEBUG)

stop = threading.Event()

config = configparser.ConfigParser()
logger.debug("Reading config file %s", args.configPath)
config.read(args.configPath)
try:
    FTPCONFIG = config["FTP"]
    PATHCONFIG = config["PATH"]
    PRINTCONFIG = config["PRINT"]
except KeyError:
    logger.critical("Failed to load config")
    exit(1)

NC_ENABLED = True
try:
    NCCONFIG = config["NC"]
except KeyError:
    NC_ENABLED = False
    logger.warning("Disabled Nextcloud, as no config has been found!")


def signalhandler(signum):
    logger.info("Signal handler called with signal {}".format(signum))
    stop.set()
    logger.warning("exiting...")
    exit(0)

def checkFTPforFiles(ftp):
    files = ftp.nlst(PATHCONFIG.get("remote"))
    files.remove(f"{PATHCONFIG.get('remote')}/.faxmeta.xml")
    if len(files) > 0:
        # Wait 10 seconds, so files which are currently written, are finished.
        time.sleep(10)
        return files
    return False


def downloadFilefromFTP(ftp, file):
    filename = PATHCONFIG.get("local") + "/" + path.basename(file)
    with open(filename, 'wb') as fp:
        ftp.retrbinary(f"RETR {file}", fp.write)


def deleteFileFromFTP(ftp, file):
    logger.info(f"Deleting {path.basename(file)} from FritzBox")
    ftp.delete(file)


def uploadFileToNC(file):
    logger.info(f"Uploading {path.basename(file)} to Nextcloud")
    try:
        nc = nextcloud_client.Client.from_public_link(NCCONFIG.get("url"))
        nc.drop_file(PATHCONFIG.get("local") + "/" + path.basename(file))
    except BaseException as e:
        logger.critical("Failed to upload to Nextcloud")
        logger.critical(e)


def printFile(file, printer, count):
    filename = PATHCONFIG.get("local") + "/" + path.basename(file)
    logger.info(f"Printing {path.basename(file)} to {printer} {count} times")
    subprocess.run(["/usr/bin/lp", "-d", printer, "-n", str(count), filename])


signal.signal(signal.SIGTERM, signalhandler)
if platform.system() == 'Linux':
    signal.signal(signal.SIGHUP, signalhandler)

try:
    while not stop.is_set():
        ftp = FTP(FTPCONFIG.get("host"))
        ftp.login(user=FTPCONFIG.get("user"), passwd=FTPCONFIG.get("pass"))
        logger.info("Checking FritzBox for new files")
        files = checkFTPforFiles(ftp)
        if files:
            for file in files:
                name = path.basename(file)
                logger.info(f"Processing {name}")
                downloadFilefromFTP(ftp, file)

                match = re.match(REGEX_FAX_NUMBER, name)
                if match is not None and match.group(1) == PRINTCONFIG.get("ILS_NUMBER"):
                    printFile(file, PRINTCONFIG.get("PRINTER"), PRINTCONFIG.get("ALERT_PRINT_COUNT"))
                else:
                    printFile(file, PRINTCONFIG.get("PRINTER"), 1)

                if NC_ENABLED:
                    uploadFileToNC(file)

                deleteFileFromFTP(ftp, file)

        ftp.quit()
        time.sleep(5)
except KeyboardInterrupt:
    signalhandler("KeyboardInterrupt")