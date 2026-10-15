from TopoPyScale import topoclass as tc

mp = tc.Topoclass('./config.yml')
mp.compute_dem_param()

print("DEM parameters computed successfully")
