import os
import hashlib
from urllib.request import urlopen
from string import Template
from dependency_lookup import get_dependency_string


template_string = """package:
  name: $full_name
  version: "$version"
source:
  url: $url
  md5: $md5
build:
  script: R CMD INSTALL --build .
  number: 0
  rpaths:
    - lib/R/lib
    - lib/
requirements:
  build:${dependencies}
  run:${dependencies}
test:
  commands:
    - '$$R -e "library(''${name}'')"'
about:
  home: ${home}
  license: $license_type
  summary: $summary
"""


def generate_meta_yaml(
        name,
        version,
        base_url,
        home,
        license_type,
        summary,
        dependencies=[],
        prefix="bioconductor-"
):
    full_file = name + "_" + version + ".tar.gz"
    full_package_name = prefix + name.lower()

    url = os.path.join(base_url, full_file)
    md5 = hashlib.md5(urlopen(url).read()).hexdigest()

    dep_text = ""
    for dep in dependencies:
        dep_string = get_dependency_string(dep)
        dep_text += "\n    - {}".format(dep_string)

    template = Template(template_string)
    text = template.substitute(
        name=name,
        full_name=full_package_name,
        version=version.replace("-", "."),
        url=url,
        md5=md5,
        home=home,
        license_type=license_type,
        dependencies=dep_text,
        summary=summary
    )

    with open("recipes/{}/meta.yaml".format(full_package_name), "w") as yml_file:
        yml_file.write(text)
