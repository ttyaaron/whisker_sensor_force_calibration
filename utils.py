import numpy as np
from scipy.spatial.transform import Rotation as R

def gen_path_spherical(p1, r=48, theta=0, phi=0, N=100):
  """ helper function to generate a path of points in spherical coordinates """
  # rotation from spherical coord to stage coordinates
  rot=R.from_euler('zx', [-90, 90], degrees=True).as_matrix()
  # spherical coordinates to cartesian conversion
  f = lambda r, theta, phi: (r*np.sin(phi)*np.cos(theta), r*np.sin(phi)*np.sin(theta), r*np.cos(phi))

  # interpolate to point farthest frmo base
  p2 = p1 + rot.dot(f(r, theta, phi))

  return np.linspace(p1, p2, N)

def gen_path_cartesian(p_orig, p_dest, N=100, mode='relative'):
  """ helper function to generate a path of points in cartesian coordinates """
  # rotation from spherical coord to stage coordinates
  rot=R.from_euler('zx', [-90, 90], degrees=True).as_matrix()
  if (mode=='relative'):
    p = p_orig + rot.dot(p_dest)
  elif (mode=='absolute'):
    p = p_dest
  # get unit vector from origin to source
  total_length = np.linalg.norm(p - p_orig)
  u = (p-p_orig)/total_length
  # repeat u to size of output array
  u = np.tile(u[np.newaxis,:], (N-2,1))
  random_scalar = total_length/(2*N)*np.random.uniform(low=-1,high=1,size=N-2)
  # scale u by random_scalar
  u *= random_scalar[:,np.newaxis]
  out = np.linspace(p_orig, p, N)
  out[1:-1,:] += u
  return out