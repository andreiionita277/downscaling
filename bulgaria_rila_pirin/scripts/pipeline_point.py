from TopoPyScale import topoclass as tc

print('\n ------------------------------- \n')
print('Point Downscaling — all 28 METER.AC stations, 2022-11-27/2022-11-28')
print('\n ------------------------------- \n')

config_file = '/app/bulgaria_rila_pirin/config_point.yml'
mq = tc.Topoclass(config_file)

print('---> STEP 1/7: get_era5')
mq.get_era5()

print('---> STEP 2/7: compute_dem_param')
mq.compute_dem_param()

print('---> STEP 3/7: extract_topo_param')
mq.extract_topo_param()

print('---> STEP 4/7: compute_solar_geometry')
mq.compute_solar_geometry()

print('---> STEP 5/7: compute_horizon')
mq.compute_horizon()

print('---> STEP 6/7: downscale_climate')
mq.downscale_climate()

print('---> STEP 7/7: to_netcdf')
mq.to_netcdf()

print('\n ------------------------------- \n')
print('Pipeline finished')
print('\n ------------------------------- \n')
