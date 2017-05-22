from pymongo import MongoClient

mongo = None

if mongo is None:
    mongo = MongoClient()
