import os
from pymongo import ASCENDING
from mongo_singleton import mongo
from create_recipe import build_package_and_deps
from dependency_lookup import UnknownDependency

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Connect to Mongo
db = mongo.bioconductor_packages
packages = db.packages
dep_lookup = db.dependency_lookup

# Reset log files
try:
    os.remove("stderr.txt")
except OSError:
    pass
try:
    os.remove("stdout.txt")
except OSError:
    pass


def get_next_package():
    return packages.find({"state": {"$eq": "NEW"}}).sort("priority", ASCENDING).next()


package_to_build = get_next_package()

while(True):
    try:
        last_build_success = build_package_and_deps(package_to_build["name"])
    except UnknownDependency as e:
        message = e.args
        packages.update_one(
            {"name": package_to_build["name"]},
            {"$set": {"state": "FAILED"}}
        )
        logger.info(("The last build command raised an UnknownDependency error for the"
                     " dependency: {}").format(message))

    package_to_build = get_next_package()
