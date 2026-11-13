# test_api.py
# Step 1 test: checks that your LTA DataMall API key works.
# It prints the next arrival time for every bus at one bus stop.

import os
import requests
from dotenv import load_dotenv

load_dotenv()                      # reads your key from the .env file
API_KEY = os.getenv("LTA_API_KEY")

BUS_STOP = "44089"                 # change to a bus stop code near you (it's on the bus stop sign)

url = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"
headers = {"AccountKey": API_KEY, "accept": "application/json"}

response = requests.get(url, headers=headers, params={"BusStopCode": BUS_STOP})

if response.status_code != 200:
    print("Something went wrong. Status code:", response.status_code)
    print("Check that your API key in .env is correct.")
else:
    data = response.json()
    print(f"Buses at stop {BUS_STOP}:")
    for service in data["Services"]:
        next_bus = service["NextBus"]
        print(f"  Bus {service['ServiceNo']:>4} | arrives {next_bus['EstimatedArrival']} | load {next_bus['Load']}")
