import requests

def get_travel_time():
    # The new GraphQL endpoint
    url = "http://localhost:8080/otp/gtfs/v1"

    # Define your coordinates and modes inside the query string
    graphql_query = """
    {
      plan(
        from: {lat: 22.2913, lon: 114.2005}
        to: {lat: 22.2081, lon: 114.0294}
        transportModes: [{mode: WALK}, {mode: TRANSIT}]
      ) {
        itineraries {
          duration
          walkTime
        }
      }
    }
    """

    # Send it as a POST request using the json parameter
    try:
        response = requests.post(url, json={'query': graphql_query})
        data = response.json()
        
        # Extract the duration of the fastest itinerary
        if data['data']['plan']['itineraries']:
            fastest_duration = data['data']['plan']['itineraries'][0]['duration']
            print(f"Travel time: {fastest_duration} seconds")
            return fastest_duration
        else:
            print("No route found.")
            return None
            
    except Exception as e:
        print(f"Error connecting to OTP: {e}")

# Run the test
get_travel_time()