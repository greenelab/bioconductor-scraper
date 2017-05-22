# biconductor-scraper
A scraper which tries to generate conda recipes for bioconductor and other needed R packages.

## Getting Started

You should make sure you have MongoDB installed and running. You should also create
a virtual environment and run `pip install -r requirements.txt` within it.

Before anything else will work, running `python generate_annotation_recipes.py`
is required. This will populate a mongo collection with metadata about packages.
Tracking all this metadata in a database allows `build_all_recipes.py` to be
stopped and restarted without losing work. Once you've done that you can either run:

```
python build_all_recipes.py
```

to build all the packages on bioconductor.org, or

```
python create_recipe.py -n <package-name>
```

to just build one package by name.

Note that this repo adheres to PEP8 standards with a 100 character line limit.
