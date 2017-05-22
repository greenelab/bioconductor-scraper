import os
from pymongo import MongoClient, ASCENDING
from create_recipe import build_package_and_deps
from dependency_lookup import UnknownDependency

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Connect to Mongo
client = MongoClient()
db = client.bioconductor_packages
packages = db.packages
dep_lookup = db.dependency_lookup


DEPENDENCY_LOOKUP_POPULATOR = "dep_lookup.py"

# Reset log files
try:
    os.remove("stderr.txt")
except OSError:
    pass
try:
    os.remove("stdout.txt")
except OSError:
    pass


def add_dep_lookup(r_name, conda_name, channel):
    logger.info(("Adding package with r_name of {r_name}, conda_name of {conda_name}, "
                 "and a channel  of {channel} to dependency lookup.").format(r_name=r_name,
                                                                             conda_name=conda_name,
                                                                             channel=channel))
    dep_lookup.insert_one({"r_name": r_name, "conda_name": conda_name, "channel": channel})

    # To make this portable-ish, save dependencies which have been figured
    # out to a file so a different mongo db could be populated with them.
    line = ('dep_lookup.insert_one({{"r_name": "{r_name}", "conda_name":'
            ' "{conda_name}", "channel": "{channel}"}})\n').format(r_name=r_name,
                                                                   conda_name=conda_name,
                                                                   channel=channel)
    with open(DEPENDENCY_LOOKUP_POPULATOR, "a") as dep_file:
        dep_file.write(line)


def get_next_package():
    return packages.find({"state": {"$eq": "NEW"}}).sort("priority", ASCENDING).next()


package_to_build = get_next_package()
last_build_success = True

while(True):
    try:
        last_build_success = build_package_and_deps(package_to_build["name"])
    except UnknownDependency as e:
        # last_build_success = False
        message, = e.args
        packages.update_one(
            {"name": package_to_build["name"]},
            {"$set": {"state": "FAILED"}}
        )
        logger.info(("The last build command raised an UnknownDependency error for the"
                     " dependency: {}").format(message))

    package_to_build = get_next_package()
