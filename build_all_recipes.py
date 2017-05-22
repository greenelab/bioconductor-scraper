import os
from pymongo import MongoClient, ASCENDING
from create_recipe import build_package_and_deps
from dependency_lookup import UnknownDependency

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


# Connect to Mongo
client = MongoClient()
db = client.bioconductor_packages
packages = db.packages
dep_lookup = db.dependency_lookup


def add_dep_lookup(r_name, conda_name, channel):
    print(("Adding package with r_name of {r_name}, conda_name of {conda_name}, "
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
    return packages.find({"state": {"$ne": "DONE"}}).sort("priority", ASCENDING).next()


package_to_build = get_next_package()
last_build_success = True

while(last_build_success):
    try:
        last_build_success = build_package_and_deps(package_to_build["name"])
    except UnknownDependency as e:
        last_build_success = False
        message, = e.args
        print(("The last build command raised an UnknownDependency error for the"
               " dependency: {}").format(message))

        if message.find(" ") != -1:
            raise

        dependency_name = input(("Enter the name of the package as it appears on "
                                 "Anaconda.org (or q to quit): "))
        if dependency_name == "q":
            exit()
        channel_name = input('Enter the name of the channel for the package (or q to quit): ')
        if channel_name == "q":
            exit()

        add_dep_lookup(message, dependency_name, channel_name)

    package_to_build = get_next_package()
