import requests
from pymongo import DESCENDING
from mongo_singleton import mongo
from lxml import etree

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


CRAN_URL_TEMPLATE = "https://cran.r-project.org/web/packages/{}/index.html"
SOURCE_URL_BASE = "https://cran.r-project.org/src/contrib/"


def scrape_cran_package(name):
    logger.info("Scraping cran package: " + name)
    # Connect to Mongo
    db = mongo.bioconductor_packages
    packages = db.packages

    url = CRAN_URL_TEMPLATE.format(name)
    request = requests.get(url)
    if request.status_code != 200:
        logger.error("Cran returned non-200 status code for package: " + name)
        return False

    html = request.text
    tables = etree.HTML(html).findall(".//table")

    package_table = {}
    for table in tables:
        rows = iter(table)
        for row in rows:
            cols = row.getchildren()
            if cols[0].text == "License:":
                package_table[cols[0].text] = cols[1].getchildren()[0].text
            else:
                package_table[cols[0].text] = cols[1].text

    highest_priority_package = packages.find({}).sort("priority", DESCENDING).next()
    priority = highest_priority_package["priority"] + 1

    packages.insert_one({
        "name": name,
        "lower_name": name.lower(),
        "version": package_table["Version:"],
        "home_url": url,
        "source_url_base": SOURCE_URL_BASE,
        "license_code": package_table["License:"],
        # Everything depends on R.
        "dependencies": [{"name": "r-base", "version": "3.3.2"}],
        "priority": priority,
        "maintainer": package_table["Maintainer:"],
        "state": "NEW",
        "source": "cran"
    })

    return True
