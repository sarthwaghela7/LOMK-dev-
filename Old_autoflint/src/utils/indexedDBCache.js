// IndexedDB cache utility for storing large datasets
// Provides much higher storage limits than localStorage (~50MB vs ~5MB)

const DB_NAME = "AutoFlintCache";
const DB_VERSION = 1;
const STORE_NAMES = {
  CALL_RECORDS: "call_records",
  CALL_ANALYSIS: "call_analysis",
};

let db = null;

// Initialize IndexedDB
export const initDB = () => {
  return new Promise((resolve, reject) => {
    if (db) {
      resolve(db);
      return;
    }

    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => {
      console.error("IndexedDB initialization failed");
      reject(request.error);
    };

    request.onsuccess = () => {
      db = request.result;
      resolve(db);
    };

    request.onupgradeneeded = (event) => {
      const database = event.target.result;
      
      // Create object stores if they don't exist
      if (!database.objectStoreNames.contains(STORE_NAMES.CALL_RECORDS)) {
        database.createObjectStore(STORE_NAMES.CALL_RECORDS);
      }
      if (!database.objectStoreNames.contains(STORE_NAMES.CALL_ANALYSIS)) {
        database.createObjectStore(STORE_NAMES.CALL_ANALYSIS);
      }
    };
  });
};

// Save data to IndexedDB
export const saveToIndexedDB = async (storeName, data) => {
  try {
    const database = await initDB();
    const transaction = database.transaction([storeName], "readwrite");
    const store = transaction.objectStore(storeName);
    
    return new Promise((resolve, reject) => {
      const request = store.put(data, "cache");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(true);
    });
  } catch (err) {
    console.warn(`Failed to save to IndexedDB (${storeName}):`, err);
    return false;
  }
};

// Retrieve data from IndexedDB
export const getFromIndexedDB = async (storeName) => {
  try {
    const database = await initDB();
    const transaction = database.transaction([storeName], "readonly");
    const store = transaction.objectStore(storeName);
    
    return new Promise((resolve, reject) => {
      const request = store.get("cache");
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result || null);
    });
  } catch (err) {
    console.warn(`Failed to retrieve from IndexedDB (${storeName}):`, err);
    return null;
  }
};

// Clear specific store
export const clearIndexedDBStore = async (storeName) => {
  try {
    const database = await initDB();
    const transaction = database.transaction([storeName], "readwrite");
    const store = transaction.objectStore(storeName);
    
    return new Promise((resolve, reject) => {
      const request = store.clear();
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(true);
    });
  } catch (err) {
    console.warn(`Failed to clear IndexedDB store (${storeName}):`, err);
    return false;
  }
};

// Clear all cache
export const clearAllIndexedDBCache = async () => {
  try {
    const database = await initDB();
    const transaction = database.transaction(Object.values(STORE_NAMES), "readwrite");
    
    const promises = Object.values(STORE_NAMES).map((storeName) => {
      return new Promise((resolve) => {
        const request = transaction.objectStore(storeName).clear();
        request.onsuccess = () => resolve(true);
        request.onerror = () => resolve(false);
      });
    });

    return Promise.all(promises);
  } catch (err) {
    console.warn("Failed to clear all IndexedDB cache:", err);
    return false;
  }
};

export const STORE_NAMES_EXPORT = STORE_NAMES;
