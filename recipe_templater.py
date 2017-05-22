from string import Template
from dependency_lookup import get_dependency_string

template_string = """package:
  name: $name
  version: "$version"

source:
  url: ${base_url}/$full_file

build:
  script: R CMD INSTALL --build .

requirements:
  build:${dependencies}
  run:${dependencies}

about:
  home: ${home}
  license: $license_type
"""


def generate_meta_yaml(
        name,
        version,
        base_url,
        home,
        license_type,
        dependencies=[],
        prefix="bioconductor-"
):
    full_file = name + "_" + version + ".tar.gz"
    full_package_name = prefix + name.lower()

    dep_text = ""
    for dep in dependencies:
        dep_string = get_dependency_string(dep)
        dep_text += "\n    - {}".format(dep_string)

    template = Template(template_string)
    text = template.substitute(
        name=full_package_name,
        version=version.replace("-", "."),
        base_url=base_url,
        full_file=full_file,
        home=home,
        license_type=license_type,
        dependencies=dep_text
    )

    with open("recipes/{}/meta.yaml".format(full_package_name), "w") as yml_file:
        yml_file.write(text)
