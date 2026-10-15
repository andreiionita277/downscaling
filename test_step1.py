from TopoPyScale import topoclass as tc

mp = tc.Topoclass('./config.yml')

print("Project name:", mp.config.project.name)
print("DEM file:", mp.config.dem.file)
print("Start / End:", mp.config.project.start, mp.config.project.end)
print("Number of clusters:", mp.config.sampling.toposub.n_clusters)
