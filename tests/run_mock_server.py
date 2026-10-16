# Runs the backend with an in-memory MongoDB mock for local testing only.
import os, sys
os.environ.setdefault("MONGO_URL", "mongodb://mock")
os.environ.setdefault("DB_NAME", "family_test")
import motor.motor_asyncio as ma
from mongomock_motor import AsyncMongoMockClient
ma.AsyncIOMotorClient = AsyncMongoMockClient
sys.path.insert(0, sys.argv[1])
import server
import uvicorn
uvicorn.run(server.app, host="127.0.0.1", port=int(sys.argv[2]), log_level="warning")
