"""This script scrapes bioconductor.org for packages and inserts
them into the bioconductor_packages.packages collection."""

import requests
from pymongo import MongoClient
from pprint import pprint
from lxml import etree

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# Connect to Mongo
client = MongoClient()
db = client.bioconductor_packages
packages = db.packages

namespaces = ["bioc", "data/annotation", "data/experiment"]

PACKAGE_LIST_URL = "https://bioconductor.org/packages/3.5/{namespace}/"
PACKAGE_URL_TEMPLATE = "https://bioconductor.org/packages/3.5/{namespace}/html/{package_name}.html"
SOURCE_URL_BASE = "https://bioconductor.org/packages/3.5/{namespace}/src/contrib"

i = 0
for namespace in namespaces:
    html = requests.get(PACKAGE_LIST_URL.format(namespace=namespace)).text

    table = etree.HTML(html).find(".//table")
    rows = iter(table)
    headers = [col.text for col in next(rows)]
    table = []
    for row in rows:
        package_name = row[0].getchildren()[0].text
        values = [col.text for col in row[1:]]
        values = [package_name] + values
        table.append(dict(zip(headers, values)))

    for row in table:
        package_name = row["Package"]
        package_url = PACKAGE_URL_TEMPLATE.format(namespace=namespace, package_name=package_name)
        package_html = requests.get(package_url).text
        parsed_html = etree.HTML(package_html)
        columns = parsed_html.findall(".//td")
        version_column = None
        license_column = None
        for column in columns:
            if column.text == "Version":
                version_column = column
            elif column.text == "License":
                license_column = column

        # href = package_source_column.getnext().getchildren()[0].attrib["href"]
        version = version_column.getnext().text
        license_code = license_column.getnext().text

        paragraphs = parsed_html.findall(".//p")
        maintainer_text = ""
        for paragraph in paragraphs:
            paragraph_text = paragraph.text
            if paragraph_text and "Maintainer" in paragraph_text:
                maintainer_text = paragraph_text

        packages.insert_one({
            "name": package_name,
            "lower_name": package_name.lower(),
            "version": version,
            "home_url": package_url,
            "source_url_base": SOURCE_URL_BASE.format(namespace=namespace),
            "license_code": license_code,
            "dependencies": [{"name": "r-base", "version": "3.3.2"}],
            "priority": i,
            "maintainer": maintainer_text,
            "state": "NEW"
        })
        i += 1
        logger.info("Generated package:" + package_name)
