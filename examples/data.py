from kairospy.data import DataStore

store = DataStore(".kairos/data")
store.write("research.signal", [
    {"time": "2026-01-01T00:00:00+00:00", "value": 1},
])
rows = store.read("research.signal")
print(rows)