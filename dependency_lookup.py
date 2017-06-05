import requests
from mongo_singleton import mongo
from pprint import pprint
from cran_scraper import scrape_cran_package

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


ANACONDA_URL_BASE = "https://anaconda.org/r/r-"

db = mongo.bioconductor_packages
packages = db.packages
dep_lookup = db.dependency_lookup


class UnknownDependency(Exception):
    pass


def populate_lookup_table():
    dep_lookup.insert_one(
        {"r_name": "RSQLite", "conda_name": "r-rsqlite", "channel": "conda-forge"})
    dep_lookup.insert_one({"r_name": "DBI", "conda_name": "r-dbi", "channel": "conda-forge"})
    dep_lookup.insert_one(
        {"r_name": "foreach", "conda_name": "r-foreach", "channel": "conda-forge"})


def get_dependency_string(dep_object):
    dep_name = dep_object["name"]

    start_string = ""
    end_string = ""
    if "version" in dep_object:
        # Only use single quotes if there's a version number
        start_string = "'"
        end_string = " >=" + dep_object["version"] + "'"

    # special case:
    if dep_name == "r-base":
        return start_string + "r-base" + end_string

    request = requests.get(ANACONDA_URL_BASE + dep_name)
    # Anaconda doesn't know how to use HTTP codes apparently, so this is the only
    # way to know we're not authenticated....
    if request.text.find("trying to access a page that requires authentication.") == -1:
        return start_string + "r-" + dep_name.lower() + end_string

    dep_lookup_entry = dep_lookup.find_one({"r_name": dep_name})
    if dep_lookup_entry is not None:
        return start_string + dep_lookup_entry["conda_name"] + end_string

    package_record = packages.find_one({"name": dep_name})
    if package_record is not None:
        if "source" in package_record and package_record["source"] == "cran":
            return start_string + "r-" + dep_name.lower() + end_string
        else:
            return start_string + "bioconductor-" + dep_name.lower() + end_string

    if scrape_cran_package(dep_name):
        return start_string + "r-" + dep_name.lower() + end_string

    logger.error("Cannot find dependency:")
    pprint(dep_object)
    raise UnknownDependency(dep_object["name"])
