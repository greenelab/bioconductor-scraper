import os
import re
import shutil
from pymongo import MongoClient
import argparse
import subprocess
from recipe_templater import generate_meta_yaml
from dependency_lookup import UnknownDependency
from cran_scraper import scrape_cran_package
from pprint import pprint


def add_dependencies_to_package(package_name, dependencies):
    print("Adding dependencies to: " + package_name)
    client = MongoClient()
    db = client.bioconductor_packages
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
    client = MongoClient()
    db = client.bioconductor_packages
    packages = db.packages

    package_object = packages.find_one({"name": package_name})
    existing_deps = package_object["dependencies"]
    dep_objects = []
    for dep in missing_deps:
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
    client = MongoClient()
    db = client.bioconductor_packages
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
    error_message = remove_weird_quotes(error_message)

    print("Handling stderr error: " + error_message)

    pattern = r"ERROR: lazy loading failed for package (.*)"
    error_match = re.match(pattern, error_message)
    if error_match is not None:

        for line in full_errors.split("\n"):
            line = remove_weird_quotes(line)

            pattern = r"  namespace (.*?) (.*?) is already loaded, but >= (.*?) is required"
            line_match = re.match(pattern, line)
            if line_match is not None:
                add_dependencies_to_package(package_name,
                                            [{"name": line_match.group(1),
                                              "version": line_match.group(3)}])
                return True

            pattern = r"Error : package (.*?) (.*?) was found, but >= (.*?) is required by .*"
            line_match = re.match(pattern, line)
            if line_match is not None:
                add_or_build_dependencies(package_name,
                                          [{"name": line_match.group(1),
                                            "version": line_match.group(3)}])
                return True

            pattern = r"Error: package or namespace load failed for (.*?):"
            line_match = re.match(pattern, line)
            if line_match is not None:
                add_or_build_dependencies(package_name,
                                          [{"name": line_match.group(1)}])
                return True

            pattern = r"  there is no package called (.*)"
            line_match = re.match(pattern, line)
            if line_match is not None:
                add_or_build_dependencies(package_name,
                                          [{"name": line_match.group(1)}])
                return True

            pattern = r"Error : package (.*?) required by (.*?) could not be found"
            line_match = re.match(pattern, line)
            if line_match is not None:
                add_or_build_dependencies(package_name,
                                          [{"name": line_match.group(1)}])
                return True

            pattern = r"Error : package (.*?) could not be loaded"
            line_match = re.match(pattern, line)
            if line_match is not None:
                add_or_build_dependencies(package_name,
                                          [{"name": line_match.group(1)}])
                return True

            pattern = r"Error : This is R (.*?), package (.*?) needs >= (.*)"
            line_match = re.match(pattern, line)
            if line_match is not None:
                print("Caught an R version error.")
                r_version = line_match.group(3)
                # Probably temporary workaround until conda-forge uploads r-base 3.4
                if r_version == "3.4":
                    r_version = "3.4.0"

                change_dependency_version(package_name, "r-base", r_version)
                # return build_package_and_deps(package_name)
                return True

    # I've seen both this and the one like it above. Not sure why the difference.
    # error_message = "ERROR: this R is version 3.3.2, package 'DelayedArray' requires R >=  3.4"
    # error_message = "ERROR: this R is version 3.3.2, package 'AnnotationFilter' requires R  >= 3.4.0"
    pattern = r"ERROR: this R is version (.*?), package '(.*?)' requires R  >= (.*)"
    error_match = re.match(pattern, error_message)
    if error_match is None:
        pattern = r"ERROR: this R is version (.*?), package '(.*?)' requires R >=  (.*)"
        error_match = re.match(pattern, error_message)
    # pprint(error_match.groups())
    if error_match is not None:
        print("Caught an R version error.")
        r_version = error_match.group(3)
        # Probably temporary workaround until conda-forge uploads r-base 3.4
        if r_version == "3.4":
            r_version = "3.4.0"

        # change_dependency_version(package_name, "r-base", r_version)
        change_dependency_version(error_match.group(2), "r-base", r_version)
        # return build_package_and_deps(package_name)
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

        print("Adding dependencies:")
        pprint(dep_objects)
        print("To package: " + package_name)
        deps_handled = add_or_build_dependencies(package_name, dep_objects)

        needy_package_name = error_match.group(2)
        if deps_handled and package_name != needy_package_name:
            print(("Package {0} depends on {1} which seems to need to be rebuilt."
                   " Recurring to do so.").format(package_name, needy_package_name))
            return build_package_and_deps(needy_package_name)
        else:
            return deps_handled

    return False


def handle_stdout_errors(package_name, stdout_string):
    client = MongoClient()
    db = client.bioconductor_packages
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
        print("No error in stdout.")

    if missing_packages_line_index != -1:
        start_index = missing_packages_line_index + 1  # don't include the message itself
        missing_package_lines = output_lines[start_index:first_empty_line_index]

        for line in missing_package_lines:
            if line.find("bioconductor-") != -1:
                dependency_name_lower = line.replace("  - bioconductor-", "")
                # If conda outputs a dependency chain like:
                # bioconductor-dnacopy -> r 3.2.2*
                # Then we just want to check for "dnacopy", not "dnacopy -> 3 3.2.2*"
                dependency_name_lower = dependency_name_lower.split(" ")[0]
                dependency_object = packages.find_one({"lower_name": dependency_name_lower})
                if dependency_object is not None:
                    dependency_name = dependency_object["name"]
                    print("Recurring to build dependency: {}".format(dependency_name))
                    build_package_and_deps(dependency_name)
                else:
                    print("Unknown dependency: {}".format(line))
                    raise UnknownDependency(line)
            elif line.find("cran-") != -1:
                dependency_name_lower = line.replace("  - cran-", "")
                dependency_object = packages.find_one({"lower_name": dependency_name_lower})
                if dependency_object is not None:
                    dependency_name = dependency_object["name"]
                    print("Recurring to build dependency: {}".format(dependency_name))
                    build_package_and_deps(dependency_name)
                else:
                    print("Unknown dependency: {}".format(line))
                    raise UnknownDependency(line)
            else:
                print("Unknown dependency: {}".format(line))
                raise UnknownDependency

    elif specification_conflict_line != -1:
        package_record = packages.find_one({"name": package_name})
        if package_record["state"] == "TRIED":
            return False
        else:
            print(("Already tried to fix this specification"
                   " error for package {}").format(package_name))
            packages.update_one({"name": package_name}, {"$set": {"state": "TRIED"}})

        print("Handling specification conflict error.")
        start_index = specification_conflict_line + 1  # don't include the message itself
        package_conflict_lines = output_lines[start_index:help_message_line_index]

        for line in package_conflict_lines:
            # Try to rebuild packages that don't specify versions
            version_specication_pattern = r".* >=.*"
            version_match = re.match(version_specication_pattern, line)
            if version_match is None:
                print("Trying to handle a specification conflict for:")
                print(line)
                # handle cran or bioconductor
                dependency_name_lower = line.replace("  - bioconductor-", "")
                dependency_name_lower = dependency_name_lower.replace("  - r-", "")
                # and until there's no more cran prefixed packages around.
                # REMOVE ME!!!!!!!!
                dependency_name_lower = dependency_name_lower.replace("  - cran-", "")
                dependency_name_lower = dependency_name_lower.split(" ")[0]

                dependency_object = packages.find_one({"lower_name": dependency_name_lower})
                if dependency_object is not None:
                    dependency_name = dependency_object["name"]
                    print("Recurring to build dependency: {}".format(dependency_name))
                    build_package_and_deps(dependency_name)
                else:
                    print("Unknown dependency: {}".format(line))
                    raise UnknownDependency(line)

    return build_error


def build_channels_string():
    client = MongoClient()
    db = client.bioconductor_packages
    dep_lookup = db.dependency_lookup

    channels_set = set()
    for lookup in dep_lookup.find():
        channels_set.add(lookup["channel"])

    channels_string = "-c kurtwheeler "
    for channel in channels_set:
        channels_string += "-c {} ".format(channel)

    return channels_string


def build_cran_package(name):
    if scrape_cran_package(name):
        return build_package_and_deps(name, True, "cran-")
    return False


def build_package_and_deps(name, destroy_work_dir=True, prefix="bioconductor-"):
    # Connect to Mongo
    client = MongoClient()
    db = client.bioconductor_packages
    packages = db.packages

    print("Building package {0}.".format(name))
    package_record = packages.find_one({"name": name})

    # Check for packages that we can't build.
    if package_record["state"] == "FAILED":
        print("Can't build package {}, it has failed in the past.".format(name))
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
        package_record["dependencies"],
        prefix
    )

    build_error = False

    # Workaround for: https://github.com/conda/conda-build/issues/2024
    if destroy_work_dir:
        print("Destroying the work dir.")
        shutil.rmtree("/home/kurt/miniconda3/conda-bld/work", ignore_errors=True)

    channels_string = build_channels_string()
    # build_command = "conda build --no-build-id {channels}recipes/{package_name}".format(
    build_command = "conda build {channels}recipes/{package_name}".format(
        channels=channels_string,
        package_name=full_package_name
    )
    print("Executing build command:")
    print(build_command)
    process = subprocess.Popen(build_command.split(),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()

    dependency_error = None
    error_string = stderr.decode("utf-8", "ignore")
    # print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    # print(error_string)
    # print("!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")
    with open("stderr.txt", "a") as stderr_file:
        stderr_file.write(error_string)

    output_string = stdout.decode("utf-8")
    with open("stdout.txt", "a") as stdout_file:
        stdout_file.write(output_string)

    for line in error_string.split("\n"):
        if (line.find("ERROR: dep") != -1 or line.find("ERROR: lazy") != -1
                or line.find("ERROR: this") != -1):
            build_error = True
            dependency_error = line

    if dependency_error is not None:
        if handle_build_errors(name, dependency_error, error_string):
            print("Tried to handle build errors, rebuilding package {}.".format(name))
            success = build_package_and_deps(name, False)
            print("Rebuilding package {0} returned {1}".format(name, success))
            build_error = not success
    else:
        if handle_stdout_errors(name, output_string):
            print("Tried to handle stdout errors, rebuilding package {}.".format(name))
            success = build_package_and_deps(name, False)
            print("Rebuilding package {0} returned {1}".format(name, success))
            build_error = not success

    if build_error:
        print("There was a build error for package {}.".format(name))
        print(error_string)
        packages.update_one(
            {"name": name},
            {"$set": {"state": "FAILED"}}
        )
        return False
    else:
        print("There was no build error for package: {0}".format(name))
        packages.update_one(
            {"name": name},
            {"$set": {"state": "DONE"}}
        )
        return True


# build_package_and_deps("pd.081229.hg18.promoter.medip.hx1")


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
