import React, { useState } from 'react';
import { 
  MapPin, Settings, AlertTriangle, CheckCircle, UploadCloud, 
  Play, Map as MapIcon, Clock, Users, Activity, Network, CheckSquare
} from 'lucide-react';

// Required for the interactive map
import 'leaflet/dist/leaflet.css';
import { MapContainer, TileLayer, CircleMarker, Polyline, Tooltip } from 'react-leaflet';

// Colors for the OR-Tools worker routes
const WORKER_COLORS = [
  '#e6194B', '#3cb44b', '#4363d8', '#f58231', '#911eb4', 
  '#42d4f4', '#f032e6', '#bfef45', '#fabed4', '#469990'
];

export default function App() {
  const [step, setStep] = useState(1);
  
  // ALL Parameters are here
  const [params, setParams] = useState({
    numWorkers: 5, 
    dailyHours: 8,
    stayTimeMins: 15,
    maxWalkDist: 1000,
    lunchBreak: true,
    lunchStart: '12:00',
    lunchDuration: 45
  });

  const defaultAddresses = "International Commerce Centre, 1 Austin Road West, Tsim Sha Tsui\n" +
    "Alexandra House, 18 Chater Road, Central\n" +
    "Amoy Gardens Block B, 77 Ngau Tau Kok Road, Kowloon Bay\n" +
    "Taikoo Shing Orchid Mansion, Taikooshing Road, Taikooshing\n" +
    "Langham Place Office Tower, 8 Argyle Street, Mong Kok\n" +
    "Custom Coordinate Test 1, 22.3190, 114.1694\n" +
    "Custom Coordinate Test 2, 22.27604, 114.14546\n" +
    "Metroplaza Tower 1, 223 Hing Fong Road, Kwai Fong";

  const [rawAddresses, setRawAddresses] = useState(defaultAddresses);
  const [locations, setLocations] = useState([]);
  const [isGeocoding, setIsGeocoding] = useState(false);
  
  const [matrixStatus, setMatrixStatus] = useState(null); 
  const [graphData, setGraphData] = useState(null);
  const [optStatus, setOptStatus] = useState(null);
  const [routes, setRoutes] = useState(null);

  const handleParamChange = (e) => {
    const { name, value, type, checked } = e.target;
    setParams(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const processAddresses = async () => {
    setIsGeocoding(true);
    setStep(3);
    setMatrixStatus(null);
    setGraphData(null);
    setRoutes(null);
    
    try {
      const addressList = rawAddresses.split('\n').filter(a => a.trim() !== '');
      // Using your Vite proxy!
      const response = await fetch('/geocode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ addresses: addressList })
      });
      
      if (!response.ok) {
        const message = await response.text();
        throw new Error(`Geocoding failed: ${response.status} ${message}`);
      }
      const data = await response.json();
      setLocations(data.results);
    } catch (error) {
      console.error("Geocoding failed:", error);
      alert(`Failed to connect to the Python backend: ${error.message}`);
    } finally {
      setIsGeocoding(false);
    }
  };

  const hasErrors = locations.some(loc => loc.status === 'error');
  const allResolved = locations.length > 0 && !hasErrors;

  const updateCoordinate = (id, field, value) => {
    setLocations(prev => prev.map(loc => {
      if (loc.id === id) {
        const updated = { ...loc, [field]: value };
        if (updated.lat && updated.lon && !isNaN(updated.lat) && !isNaN(updated.lon)) {
          updated.status = 'ok';
          updated.name = 'Manually Resolved';
        } else {
          updated.status = 'error';
        }
        return updated;
      }
      return loc;
    }));
  };

  const buildMatrix = async () => {
    setMatrixStatus('processing');
    try {
      const response = await fetch('/build-matrix', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locations, params })
      });
      
      if (!response.ok) {
        const message = await response.text();
        throw new Error(`Matrix build failed: ${response.status} ${message}`);
      }
      const data = await response.json();
      
      console.log("\n✅ SPARSE MATRIX GENERATED");
      console.log("OTP Matrix Array:", data.matrix);

      setGraphData(data);
      setMatrixStatus('complete');
    } catch (error) {
      console.error("Matrix generation failed:", error);
      setMatrixStatus('error');
    }
  };

  const runOptimization = async () => {
    setOptStatus('processing');
    try {
      const response = await fetch('/optimize', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ locations, matrix: graphData.matrix, params })
      });
      
      if (!response.ok) {
        const message = await response.text();
        throw new Error(`Optimization failed: ${response.status} ${message}`);
      }
      const data = await response.json();
      
      console.log("OR-Tools Routes:", data);
      setRoutes(data.routes);
      setOptStatus('complete');
    } catch (error) {
      console.error("Optimization failed:", error);
      setOptStatus('error');
    }
  };

  // Helper for Leaflet to auto-zoom to fit all pins
  const getMapBounds = () => {
    if (!locations.length) return [[22.2, 114.1], [22.4, 114.2]];
    const lats = locations.map(l => parseFloat(l.lat));
    const lons = locations.map(l => parseFloat(l.lon));
    return [
      [Math.min(...lats) - 0.01, Math.min(...lons) - 0.01],
      [Math.max(...lats) + 0.01, Math.max(...lons) + 0.01]
    ];
  };

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col font-sans">
      <header className="bg-blue-900 text-white p-4 shadow-md flex items-center justify-between">
        <div className="flex items-center space-x-2">
          <Network className="w-6 h-6" />
          <h1 className="text-xl font-bold tracking-wide">VRP Matrix Builder & Optimizer</h1>
        </div>
      </header>

      <main className="flex-1 max-w-6xl w-full mx-auto p-6 grid grid-cols-1 md:grid-cols-3 gap-6">
        
        {/* Left Sidebar - Params */}
        <div className="col-span-1 space-y-4">
          <div className="bg-white p-5 rounded-xl shadow-sm border border-gray-200">
            <h2 className="text-lg font-semibold mb-4 flex items-center text-gray-800">
              <Settings className="w-5 h-5 mr-2 text-blue-600" /> Optimization Params
            </h2>
            <div className="space-y-4 text-sm">
              <div>
                <label className="flex items-center text-gray-600 mb-1"><Users className="w-4 h-4 mr-1" /> Number of Workers</label>
                <input type="number" name="numWorkers" value={params.numWorkers} onChange={handleParamChange} className="w-full p-2 border rounded-md outline-none focus:border-blue-400" />
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="flex items-center text-gray-600 mb-1"><Clock className="w-4 h-4 mr-1" /> Daily Hrs</label>
                  <input type="number" name="dailyHours" value={params.dailyHours} onChange={handleParamChange} className="w-full p-2 border rounded-md outline-none" />
                </div>
                <div>
                  <label className="flex items-center text-gray-600 mb-1"><MapPin className="w-4 h-4 mr-1" /> Stay (Min)</label>
                  <input type="number" name="stayTimeMins" value={params.stayTimeMins} onChange={handleParamChange} className="w-full p-2 border rounded-md outline-none" />
                </div>
              </div>
              <div>
                <label className="flex items-center text-gray-600 mb-1">
                  <Activity className="w-4 h-4 mr-1" /> Max Walk Dist (m)
                </label>
                <input type="number" name="maxWalkDist" value={params.maxWalkDist} onChange={handleParamChange} className="w-full p-2 border rounded-md outline-none focus:border-blue-400" />
              </div>
              
              <div className="pt-2 border-t mt-4">
                <label className="flex items-center text-gray-800 font-medium mb-2 cursor-pointer">
                  <input type="checkbox" name="lunchBreak" checked={params.lunchBreak} onChange={handleParamChange} className="mr-2 rounded text-blue-600" />
                  Include Lunch Break
                </label>
                {params.lunchBreak && (
                  <div className="grid grid-cols-2 gap-2 pl-6">
                    <div>
                      <label className="text-gray-500 text-xs">Start Time</label>
                      <input type="time" name="lunchStart" value={params.lunchStart} onChange={handleParamChange} className="w-full p-1.5 border rounded-md text-sm outline-none" />
                    </div>
                    <div>
                      <label className="text-gray-500 text-xs">Duration (Min)</label>
                      <input type="number" name="lunchDuration" value={params.lunchDuration} onChange={handleParamChange} className="w-full p-1.5 border rounded-md text-sm outline-none" />
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Right Content Area */}
        <div className="col-span-2 space-y-6">
          
          {/* Step 1: Input Addresses */}
          <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
            <h2 className="text-xl font-semibold mb-2 flex items-center">
              <span className="bg-blue-100 text-blue-800 w-8 h-8 rounded-full flex items-center justify-center mr-3">1</span>
              Locations
            </h2>
            <textarea
              className="w-full h-32 p-3 border rounded-md text-sm font-mono bg-gray-50 focus:bg-white outline-none"
              value={rawAddresses}
              onChange={(e) => setRawAddresses(e.target.value)}
              disabled={isGeocoding}
            />
            <div className="mt-4 flex justify-end">
              <button onClick={processAddresses} disabled={isGeocoding} className="bg-blue-600 hover:bg-blue-700 text-white px-6 py-2 rounded-md font-medium flex items-center disabled:opacity-50">
                {isGeocoding ? 'Geocoding...' : 'Start Geocoding'} {!isGeocoding && <UploadCloud className="w-4 h-4 ml-2" />}
              </button>
            </div>
          </div>

          {/* Step 2: Errors */}
          {locations.length > 0 && hasErrors && (
            <div className="bg-white p-6 rounded-xl shadow-sm border border-red-400">
              <h2 className="text-xl font-semibold mb-4 text-red-800 flex items-center"><AlertTriangle className="w-5 h-5 mr-2" /> Geocoding Errors</h2>
              <div className="max-h-60 overflow-y-auto border rounded-md divide-y">
                {locations.filter(l => l.status === 'error').map(loc => (
                  <div key={loc.id} className="p-3 bg-red-50/50 flex justify-between">
                    <p className="text-sm">{loc.originalAddress}</p>
                    <div className="flex space-x-2">
                      <input type="text" placeholder="Lat" value={loc.lat} onChange={(e) => updateCoordinate(loc.id, 'lat', e.target.value)} className="w-24 p-1 border border-red-300 rounded text-sm" />
                      <input type="text" placeholder="Lon" value={loc.lon} onChange={(e) => updateCoordinate(loc.id, 'lon', e.target.value)} className="w-24 p-1 border border-red-300 rounded text-sm" />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Step 3: Matrix Map */}
          {allResolved && (
            <div className="bg-white p-6 rounded-xl shadow-sm border border-gray-200">
              <h2 className="text-xl font-semibold mb-2 flex items-center">
                <span className="bg-blue-900 text-white w-8 h-8 rounded-full flex items-center justify-center mr-3">2</span>
                Build Network Graph
              </h2>
              
              {!matrixStatus ? (
                <button onClick={buildMatrix} className="w-full bg-slate-800 hover:bg-slate-700 text-white py-3 rounded-lg font-semibold flex items-center justify-center">
                  <Play className="w-5 h-5 mr-2" /> Compute Graph (KNN + MST)
                </button>
              ) : (
                <div className="space-y-4">
                  {matrixStatus === 'processing' && <p className="animate-pulse font-mono text-sm">&gt; Processing spatial index...</p>}
                  
                  {matrixStatus === 'complete' && graphData && (
                    <>
                      {/* LEAFLET MAP: Raw Network Topology */}
                      <div className="w-full border rounded-lg overflow-hidden relative z-0" style={{ height: '400px' }}>
                        <MapContainer bounds={getMapBounds()} style={{ height: '100%', width: '100%' }}>
                          <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                          
                          {/* Draw KNN+MST Edges */}
                          {graphData.edges.map((edge, i) => {
                            const from = locations.find(l => l.id === edge.source);
                            const to = locations.find(l => l.id === edge.target);
                            if (!from || !to) return null;
                            return <Polyline key={i} positions={[[from.lat, from.lon], [to.lat, to.lon]]} color="#64748b" weight={2} opacity={0.6} />;
                          })}
                          
                          {/* Draw Location Dots */}
                          {locations.map(loc => (
                            <CircleMarker key={loc.id} center={[loc.lat, loc.lon]} radius={loc.id === 0 ? 7 : 5} color="white" weight={2} fillColor={loc.id === 0 ? "#dc2626" : "#2563eb"} fillOpacity={1}>
                              <Tooltip>{loc.name.substring(0, 20)}</Tooltip>
                            </CircleMarker>
                          ))}
                        </MapContainer>
                      </div>

                      <button onClick={runOptimization} disabled={optStatus === 'processing'} className="w-full bg-green-600 hover:bg-green-700 text-white py-3 rounded-lg font-bold shadow flex items-center justify-center mt-4">
                        {optStatus === 'processing' ? 'Solving...' : 'Proceed to Optimization Engine (OR-Tools)'}
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Step 4: Route Map and Results */}
          {routes && (
            <div className="bg-white p-6 rounded-xl shadow-sm border border-green-500 bg-green-50">
              <h2 className="text-xl font-semibold mb-4 text-green-900 flex items-center">
                <CheckSquare className="w-6 h-6 mr-2" /> Optimal Routes Generated
              </h2>
              
              {/* LEAFLET MAP: Final Worker Routes */}
              <div className="w-full border border-green-300 rounded-lg overflow-hidden relative z-0 mb-4 shadow-sm" style={{ height: '500px' }}>
                <MapContainer bounds={getMapBounds()} style={{ height: '100%', width: '100%' }}>
                  <TileLayer url="https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png" />
                  
                  {routes.map((route, i) => {
                    const color = WORKER_COLORS[route.worker_id % WORKER_COLORS.length];
                    const positions = route.sequence.map(nodeId => {
                      const loc = locations.find(l => l.id === nodeId);
                      return [loc.lat, loc.lon];
                    });
                    
                    return (
                      <React.Fragment key={i}>
                        {/* The solid colored line for the route sequence */}
                        <Polyline positions={positions} color={color} weight={4} opacity={0.8} />
                        
                        {/* The colored dots on the map */}
                        {route.sequence.map((nodeId, idx) => {
                          const loc = locations.find(l => l.id === nodeId);
                          const isDepot = loc.id === 0;
                          return (
                            <CircleMarker key={`${i}-${idx}`} center={[loc.lat, loc.lon]} radius={isDepot ? 7 : 5} color="white" weight={2} fillColor={isDepot ? "#000000" : color} fillOpacity={1}>
                              <Tooltip>Worker {route.worker_id + 1}: {loc.name}</Tooltip>
                            </CircleMarker>
                          );
                        })}
                      </React.Fragment>
                    );
                  })}
                </MapContainer>
              </div>

              {/* Text Logs below the Map */}
              <div className="space-y-3">
                {routes.map((route, i) => {
                  const color = WORKER_COLORS[route.worker_id % WORKER_COLORS.length];
                  return (
                    <div key={i} className="bg-white border rounded p-3 text-sm shadow-sm border-l-4" style={{ borderLeftColor: color }}>
                      <p className="font-bold text-gray-800 mb-2">Worker {route.worker_id + 1} <span className="text-gray-500 font-normal ml-2">({(route.time_seconds / 3600).toFixed(1)} hrs shift)</span></p>
                      <div className="flex flex-wrap items-center text-gray-600">
                        {route.sequence.map((nodeId, idx) => {
                          const loc = locations.find(l => l.id === nodeId);
                          const isDepot = loc.id === 0;
                          return (
                            <React.Fragment key={idx}>
                              {idx > 0 && <span className="mx-1">→</span>}
                              <span className={isDepot ? "bg-gray-800 text-white px-2 py-0.5 rounded text-xs" : "px-1"}>
                                {isDepot ? "Depot" : (loc ? loc.name.substring(0,10) : nodeId)}
                              </span>
                            </React.Fragment>
                          );
                        })}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}