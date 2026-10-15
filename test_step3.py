from TopoPyScale import topoclass as tc

mp = tc.Topoclass('./config.yml')
mp.compute_dem_param()
mp.extract_topo_param()

print("Clustering completed successfully")
print("Number of clusters:", mp.config.sampling.toposub.n_clusters)
