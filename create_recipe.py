import os
import re
import shutil
from mongo_singleton import mongo
import argparse
import subprocess
from recipe_templater import generate_meta_yaml
from dependency_lookup import UnknownDependency
from cran_scraper import scrape_cran_package
from pprint import pprint

# Import and set logger
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def add_dependencies_to_package(package_name, dependencies):
    logger.info("Adding dependencies:")
    pprint(dependencies)
    logger.info("To package: " + package_name)
    db = mongo.bioconductor_packages
    packages = db.packages

    package_object = packages.find_one({"name": package_name})
    package_deps = package_object["dependencies"]
    package_deps += dependencies

    packages.update_one(
        {"name": package_name},
        {"$set": {"dependencies": package_deps}}
    )


def add_or_build_dependencies(package_name, missing_deps):
    """For each dependency in missing_deps: if dependency already exists on package,
    build the dependency. Otherwise just add it to the package's dependencies."""
    success = True
    db = mongo.bioconductor_packages
    packages = db.packages

    package_object = packages.find_one({"name": package_name})
    existing_deps = package_object["dependencies"]
    dep_objects = []
    for dep in missing_deps:
        dep_package = packages.find_one({"name": dep["name"]})
        if dep_package is not None and dep_package["state"] == "FAILED":
            logger.error("Dependency %s has failed before, not adding it to %s.",
                         dep["name"],
                         package_name)
            return False
        if len(list(filter(lambda d: d["name"] == dep["name"], existing_deps))) > 0:
            # The dependency is already listed for the package, let's try building it.
            success = success and build_package_and_deps(dep["name"])
        else:
            # It's not a known dependency, just try adding it.
            dep_objects.append(dep)

    if len(dep_objects) > 0:
        add_dependencies_to_package(package_name, dep_objects)

    return success


def change_dependency_version(package_name, dependency_package, new_version):
    db = mongo.bioconductor_packages
    packages = db.packages

    package_object = packages.find_one({"name": package_name})
    package_deps = package_object["dependencies"]
    package_deps = list(filter(lambda d: d["name"] != dependency_package, package_deps))
    package_deps.append({"name": dependency_package, "version": new_version})

    packages.update_one(
        {"name": package_name},
        {"$set": {"dependencies": package_deps}}
    )


def remove_weird_quotes(line):
    return line.replace("‘", "").replace("’", "")


def handle_build_errors(package_name, error_message, full_errors):
    """Handles errors output via standard out. Basically just trying
    different patterns that have been observed to be output by
    `conda build`. The errors are resolved by adding dependencies to
    packages or updating the versions of dependencies."""
    error_message = remove_weird_quotes(error_message)

    logger.info("Handling stderr error: " + error_message)

    pattern = r"ERROR: lazy loading failed for package (.*)"
    error_match = re.match(pattern, error_message)
    if error_match is not None:

        for line in full_errors.split("\n"):
            line = remove_weird_quotes(line)

            pattern = r"  namespace (.*?) (.*?) is already loaded, but >= (.*?) is required"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_dependencies_to_package(package_name,
                                                   [{"name": line_match.group(1),
                                                     "version": line_match.group(3)}])

            pattern = r"Error : package (.*?) (.*?) is loaded, but >= (.*?) is required by .*"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_dependencies_to_package(package_name,
                                                   [{"name": line_match.group(1),
                                                     "version": line_match.group(3)}])

            pattern = r"Error : package (.*?) (.*?) was found, but >= (.*?) is required by .*"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_or_build_dependencies(package_name,
                                                 [{"name": line_match.group(1),
                                                   "version": line_match.group(3)}])

            pattern = r"Error : \.onLoad failed in loadNamespace\(\) for '(.*?)', details:"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_or_build_dependencies(package_name,
                                                 [{"name": line_match.group(1)}])

            pattern = r"Error: package or namespace load failed for (.*?):"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_or_build_dependencies(package_name,
                                                 [{"name": line_match.group(1)}])

            pattern = r"  there is no package called (.*)"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_or_build_dependencies(package_name,
                                                 [{"name": line_match.group(1)}])

            pattern = r"Error : package (.*?) required by (.*?) could not be found"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_or_build_dependencies(package_name,
                                                 [{"name": line_match.group(1)}])

            pattern = r"Error : package (.*?) could not be loaded"
            line_match = re.match(pattern, line)
            if line_match is not None:
                return add_or_build_dependencies(package_name,
                                                 [{"name": line_match.group(1)}])

            pattern = r"Error : This is R (.*?), package (.*?) needs >= (.*)"
            line_match = re.match(pattern, line)
            if line_match is not None:
                logger.info("Caught an R version error.")
                r_version = line_match.group(3)
                # Probably temporary workaround until conda-forge uploads r-base 3.4
                if r_version == "3.4":
                    r_version = "3.4.0"

                change_dependency_version(package_name, "r-base", r_version)
                return True

    # Conda will output 'R >= 3.3.3' with different spacing.... i.e.:
    # 'R  >= 3.3.3' or 'R >=  3.3.3'
    pattern = r"ERROR: this R is version (.*?), package '(.*?)' requires R  >= (.*)"
    error_match = re.match(pattern, error_message)
    if error_match is None:
        pattern = r"ERROR: this R is version (.*?), package '(.*?)' requires R >=  (.*)"
        error_match = re.match(pattern, error_message)

    if error_match is None:
        pattern = r"ERROR: this R is version (.*?), package '(.*?)' requires R >= (.*)"
        error_match = re.match(pattern, error_message)

    if error_match is not None:
        logger.info("Caught an R version error.")
        r_version = error_match.group(3)
        # Probably temporary workaround until conda-forge uploads r-base 3.4
        if r_version == "3.4":
            r_version = "3.4.0"

        change_dependency_version(error_match.group(2), "r-base", r_version)
        return True

    # Conda likes to pluralize error messages correctly, makes parsing them annoying.
    pattern = r"ERROR: dependency (.*?) is not available for package (.*)"
    error_match = re.match(pattern, error_message)
    if error_match is None:
        pattern = r"ERROR: dependencies (.*?) are not available for package (.*)"
        error_match = re.match(pattern, error_message)

    if error_match is not None:
        error_message = error_match.group(1).replace(",", "")
        missing_deps = error_message.split(" ")

        dep_objects = []
        for dep in missing_deps:
            dep_objects.append({"name": dep})

        logger.info("Adding dependencies:")
        pprint(dep_objects)
        logger.info("To package: " + package_name)
        deps_handled = add_or_build_dependencies(package_name, dep_objects)

        needy_package_name = error_match.group(2)
        if deps_handled and package_name != needy_package_name:
            logger.info(("Package {0} depends on {1} which seems to need to be rebuilt."
                         " Recurring to do so.").format(package_name, needy_package_name))
            return build_package_and_deps(needy_package_name)
        else:
            return deps_handled

    return False


def build_dependency(package_name, dependency_object):
    db = mongo.bioconductor_packages
    packages = db.packages
    dependency_name = dependency_object["name"]

    if dependency_object["state"] != "FAILED":
        logger.info("Recurring to build dependency: {}".format(dependency_name))
        return build_package_and_deps(dependency_name)
    else:
        logger.error("Dependency %s failed, so %s must fail as well.",
                     dependency_name,
                     package_name)
        packages.update_one(
            {"name": package_name},
            {"$set": {"state": "FAILED"}}
        )
        return False


def handle_stdout_errors(package_name, stdout_string):
    """This function is a bit of a mess but there are several types
    of errors which can be contained in standard out.
    The strategy is to loop through it once and determine what kind of
    error there is, then loop through it again to parse out the error
    details so that they can be corrected."""
    db = mongo.bioconductor_packages
    packages = db.packages
    build_error = False
    output_lines = stdout_string.split("\n")

    missing_packages_line_index = -1
    first_empty_line_index = -1
    specification_conflict_line = -1
    help_message_line_index = -1
    for i, line in enumerate(output_lines):
        if line.find("missing in current linux-64 channels:") != -1:
            build_error = True
            missing_packages_line_index = i

        if (missing_packages_line_index != -1
            and first_empty_line_index == -1
                and line == ""):
            first_empty_line_index = i

        if line.find("The following specifications were found to be in conflict:") != -1:
            build_error = True
            specification_conflict_line = i

        help_message = 'Use "conda info <package>" to see the dependencies for each package.'
        if (specification_conflict_line != -1
            and help_message_line_index == -1
                and line == help_message):
            help_message_line_index = i

    if missing_packages_line_index == -1 and specification_conflict_line == -1:
        logger.info("No error in stdout.")

    if missing_packages_line_index != -1:
        start_index = missing_packages_line_index + 1  # don't include the message itself
        missing_package_lines = output_lines[start_index:first_empty_line_index]

        for line in missing_package_lines:
            if line.find("bioconductor-") != -1:
                dependency_name_lower = line.replace("  - bioconductor-", "")
                # If conda outputs a dependency chain like:
                # bioconductor-dnacopy -> r 3.2.2*
                # Then we just want to check for "dnacopy", not "dnacopy -> r 3.2.2*"
                dependency_name_lower = dependency_name_lower.split(" ")[0]
                dependency_object = packages.find_one({"lower_name": dependency_name_lower})
                if dependency_object is not None:
                    build_dependency(package_name, dependency_object)
                else:
                    logger.info("Unknown dependency: {}".format(line))
                    raise UnknownDependency(line)
            elif line.find("cran-") != -1:
                dependency_name_lower = line.replace("  - cran-", "")
                dependency_object = packages.find_one({"lower_name": dependency_name_lower})
                if dependency_object is not None:
                    build_dependency(package_name, dependency_object)
                else:
                    logger.info("Unknown dependency: {}".format(line))
                    raise UnknownDependency(line)
            else:
                logger.info("Unknown dependency: {}".format(line))
                raise UnknownDependency

    elif specification_conflict_line != -1:
        package_record = packages.find_one({"name": package_name})
        if package_record["state"] == "TRIED":
            logger.info(("Already tried to fix this specification"
                         " error for package {}").format(package_name))
            packages.update_one({"name": package_name}, {"$set": {"state": "FAILED"}})
            return True
        else:
            packages.update_one({"name": package_name}, {"$set": {"state": "TRIED"}})

        logger.info("Handling specification conflict error.")
        start_index = specification_conflict_line + 1  # don't include the message itself
        package_conflict_lines = output_lines[start_index:help_message_line_index]

        for line in package_conflict_lines:
            # Try to rebuild packages that don't specify versions
            version_specication_pattern = r".* >=.*"
            version_match = re.match(version_specication_pattern, line)
            if version_match is None:
                logger.info("Trying to handle a specification conflict for:")
                logger.info(line)
                dependency_name_lower = line.replace("  - bioconductor-", "")
                dependency_name_lower = dependency_name_lower.replace("  - r-", "")
                dependency_name_lower = dependency_name_lower.replace("  - cran-", "")
                dependency_name_lower = dependency_name_lower.split(" ")[0]

                dependency_object = packages.find_one({"lower_name": dependency_name_lower})
                if dependency_object is not None:
                    dependency_name = dependency_object["name"]
                    logger.info("Recurring to build dependency: {}".format(dependency_name))
                    build_package_and_deps(dependency_name)
                else:
                    logger.info("Unknown dependency: {}".format(line))
                    raise UnknownDependency(line)

    return build_error


def catch_and_handle_errors(package_name, stderr, stdout):
    """Determine if an error is in standard error, if it is, handle it.
    Otherwise handle standard out errors."""
    dependency_error = None
    build_error = False
    for line in stderr.split("\n"):
        if (line.find("ERROR: dep") != -1 or line.find("ERROR: lazy") != -1
                or line.find("ERROR: this") != -1):
            build_error = True
            dependency_error = line
        elif line.find("ERROR: compilation") != -1:
            return True

    if dependency_error is not None:
        if handle_build_errors(package_name, dependency_error, stderr):
            logger.info(("Tried to handle build errors,"
                         " rebuilding package {}.").format(package_name))
            success = build_package_and_deps(package_name, False)
            logger.info("Rebuilding package {0} returned {1}".format(package_name, success))
            build_error = not success
    else:
        if handle_stdout_errors(package_name, stdout):
            logger.info(("Tried to handle stdout errors,"
                         " rebuilding package {}.").format(package_name))
            success = build_package_and_deps(package_name, False)
            logger.info("Rebuilding package {0} returned {1}".format(package_name, success))
            build_error = not success

    return build_error


def build_channels_string():
    db = mongo.bioconductor_packages
    dep_lookup = db.dependency_lookup

    channels_set = set()
    for lookup in dep_lookup.find():
        channels_set.add(lookup["channel"])

    channels_string = ""
    for channel in channels_set:
        channels_string += "-c {} ".format(channel)

    return channels_string


def build_cran_package(name):
    if scrape_cran_package(name):
        return build_package_and_deps(name, True, "cran-")
    return False


def run_conda_build(full_package_name):
    channels_string = build_channels_string()
    build_command = "conda build {channels}recipes/{package_name}".format(
        channels=channels_string,
        package_name=full_package_name
    )
    logger.info("Executing build command:")
    logger.info(build_command)
    process = subprocess.Popen(build_command.split(),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    error_string = stderr.decode("utf-8", "ignore")
    with open("stderr.txt", "a") as stderr_file:
        stderr_file.write(error_string)

    output_string = stdout.decode("utf-8")
    with open("stdout.txt", "a") as stdout_file:
        stdout_file.write(output_string)

    return (output_string, error_string)


def build_package_and_deps(name, destroy_work_dir=True, prefix="bioconductor-"):
    db = mongo.bioconductor_packages
    packages = db.packages

    logger.info("Building package {0}.".format(name))
    package_record = packages.find_one({"name": name})

    # Check for packages that we can't build.
    if package_record["state"] == "FAILED":
        logger.info("Can't build package {}, it has failed in the past.".format(name))
        return False

    if "source" in package_record and package_record["source"] == "cran":
        prefix = "cran-"

    full_package_name = prefix + package_record["lower_name"]
    os.makedirs("recipes/{}".format(full_package_name), exist_ok=True)

    generate_meta_yaml(
        package_record["name"],
        package_record["version"],
        package_record["source_url_base"],
        package_record["home_url"],
        package_record["license_code"],
        package_record["summary"],
        package_record["dependencies"],
        prefix
    )

    build_error = False

    # Workaround for: https://github.com/conda/conda-build/issues/2024
    if destroy_work_dir:
        logger.info("Destroying the work dir.")
        shutil.rmtree("/home/kurt/miniconda3/conda-bld/work", ignore_errors=True)

    output_string, error_string = run_conda_build(full_package_name)

    build_error = catch_and_handle_errors(name, error_string, output_string)

    if build_error:
        logger.info("There was a build error for package {}.".format(name))
        logger.info(error_string)
        packages.update_one(
            {"name": name},
            {"$set": {"state": "FAILED"}}
        )
        return False
    else:
        logger.info("There was no build error for package: {0}".format(name))
        packages.update_one(
            {"name": name},
            {"$set": {"state": "DONE"}}
        )
        return True


def main():
    # Reset log files
    try:
        os.remove("stderr.txt")
    except OSError:
        pass
    try:
        os.remove("stdout.txt")
    except OSError:
        pass

    # Parse out the name arg
    parser = argparse.ArgumentParser(
        description='Generates a conda meta.yaml file.')
    parser.add_argument(
        '-n', '--name', help='The name of the conda package.', required=True)

    args = vars(parser.parse_args())
    package_name = args["name"]

    build_package_and_deps(package_name)


if __name__ == "__main__":
    main()
