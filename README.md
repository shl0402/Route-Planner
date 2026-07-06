# Hong Kong Transit Routing & VRP Optimizer

This repository contains a dual-application system powered by OpenTripPlanner (OTP). It provides both a standard Point-A-to-Point-B transit routing UI (`app.py`) and a Vehicle Routing Problem (VRP) test server (`test_server.py`) for building travel-time matrices and optimizing worker routes.

## 📋 Prerequisites

Before running the applications, ensure your system has the following installed:
* **Python 3.8+**
* **Java 25** (Required for OpenTripPlanner 2.9.0 [Download Oracle Java 25 here](https://www.oracle.com/java/technologies/downloads/)).
* **OpenTripPlanner:** Ensure `otp-shaded-2.9.0.jar` is in your root directory. [Download OpenTripPlanner here](https://github.com/opentripplanner/OpenTripPlanner/releases)

---

## 🛠️ Step 1: Initial Setup

First, clone this repository (or navigate to your project directory) and install the required Python dependencies:

```bash
pip install -r requirements.txt
```

---

## 📥 Step 2: Data Procurement

To generate accurate routes, you need the latest Hong Kong transit and street data. Download the following "unprocessed" raw files:

1. **Bus GTFS Data**
   * Go to: [HK TD Headway Data](https://data.gov.hk/en-data/dataset/hk-td-tis_11-pt-headway-en)
   * Download the following 3 zipped files:
     * *Zipped headway information (Traditional Chinese version)*
     * *Zipped headway information (English version)*
     * *Zipped headway information (Simplified Chinese version)*

2. **MTR & Light Rail GTFS Data**
   * Go to: [MTR Routes & Fares Data](https://data.gov.hk/en-data/dataset/mtr-data-routes-fares-barrier-free-facilities)
   * Download the following files:
     * *MTR Lines (except Light Rail) & Stations*
     * *Light rail routes and stops*

3. **OpenStreetMap Street Data**
   * Go to: [Geofabrik Asia (Hong Kong)](https://download.geofabrik.de/asia/china/hong-kong.html)
   * Download the `.osm.pbf` file.
   * *Note: Street data changes frequently. It is recommended to update this file regularly.*

---

## ⚙️ Step 3: Data Processing

The raw data downloaded in Step 2 cannot be read directly by OTP. We must process it using the provided Python scripts.

1. **Merge Languages:** Run the language merger to combine the 3 Bus GTFS zip files into one multilingual file.
   ```bash
   python merge_lang.py
   ```
2. **Build Bus & MTR Data:** Run the builder scripts to extract the useful routing information.
   ```bash
   python gtfs_builder(bus).py
   python gtfs_builder(mtr).py
   ```

**🎯 Final Data Output:** If successful, you will generate two highly accurate zip files: `gtfs_merged_multilingual.zip` and `mtr-gtfs-accurate.zip`. 

Move these two files, along with your downloaded `.osm.pbf` file, into a folder named `data` in your project root. **Remove all other unprocessed zip files from this directory to prevent build errors.**

Your structure should look like this:
```text
project_root/
├── data/
│   ├── gtfs_merged_multilingual.zip
│   ├── mtr-gtfs-accurate.zip
│   └── hong-kong-latest.osm.pbf
├── otp-shaded-2.9.0.jar
├── app.py
└── test_server.py
```

---

## 🗺️ Step 4: Build the OTP Graph

With the data staged, OpenTripPlanner must compile it into a traversable graph. Open your terminal in the project root and run:

```bash
java -Xmx4G -jar otp-shaded-2.9.0.jar --build --save ./data
```

> **⚠️ Troubleshooting Tip:** Compiling transit graphs is memory-intensive. If the build fails or throws an OutOfMemory error, increase the allocated RAM from `4G` to `8G` by running: `java -Xmx8G -jar otp-shaded-2.9.0.jar --build --save ./data`

Once complete, a `graph.obj` file will be generated inside your `/data` folder.

---

## 🚀 Step 5: Running the Applications

To use the system, you will need to run the OTP backend server alongside the Python application of your choice.

### Terminal 1: Start the OpenTripPlanner Server
This must be running in the background for either app to function.
```bash
java -Xmx4G -jar otp-shaded-2.9.0.jar --load ./data
```
Wait until the terminal logs indicate the server is listening on port `8080`.

---

### Terminal 2: Start Your Chosen Application

**Option A: The Standard Routing App (`app.py`)**
This app provides a user interface for finding optimized routes between two specific locations.
1. Run the server:
   ```bash
   python app.py
   ```
2. Open your web browser and navigate to: `http://127.0.0.1:5000`

**Option B: The VRP Matrix Optimizer (`test_server.py`)**
This app computes travel-time matrices and visualizes optimized route distributions for multiple workers/locations.
1. Run the backend API server:
   ```bash
   python test_server.py
   ```
2. **Do not open the localhost link in your browser.** Instead, open your file explorer, locate the standalone `index.html` file in the root directory (⚠️ *Ensure it is the one in the root directory, NOT the one inside the `/templates` folder*), and double-click it to open it directly in your web browser. 

From this UI, you can input locations, adjust parameters, compute the optimal travel matrix, and visualize the optimized worker routes.
