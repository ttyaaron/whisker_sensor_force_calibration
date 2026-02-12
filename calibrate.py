import numpy as np
import yaml
import pandas as pd
import time
import datetime
import argparse
from multiprocessing.shared_memory import SharedMemory
from calib_manager import WhiskerCalibrationManager
from utils import gen_path_cartesian, gen_path_spherical
import hydra

# TODO: collect time/index during the calibration proess as well, otherwise it would be difficult to track frequency

@hydra.main(config_path="config", config_name="config", version_base=None)
def main(args):
  """ start off data-logging thread """
  args = args.stage
  N = 1 # TODO: if later calibrate multiple sensors, add this to config
  if not args.no_sensor:
    # if using whisker array then start serial and data logging thread
    # refer to the shared memory created by sensor_reading.py
    sharedMem = SharedMemory(name='sensor_data', create=False)
    # read data from the sensor
    sensor_arr = np.ndarray((N, args.data_dim), dtype=np.float32, buffer=sharedMem.buf)
  else:
    sensor_arr = np.zeros((N,args.data_dim))

  """ initialize calibration manager """
  calib_manager = WhiskerCalibrationManager(reset_pos=args.reset_pos,
                                            origin_pos=args.origin_pos,
                                            do_home=args.home)

  """ start calibration procedure """
  data = []
  month_now = datetime.datetime.now().strftime("%B")
  day_now = datetime.datetime.now().strftime("%d")
  time_now = datetime.datetime.now().strftime("%H%M")

  if args.mode == 'print':
    # Do not use While loop to get position when using the nobe to adjust motor position
    # Continuous position readings through serial might cause error in message receiving
    # Position readings in x, y, z might swap 
    while True:
      input("Please press enter to get position...")
      zaber_pos = calib_manager.get_pos()
      print(f"zaber position\n- {zaber_pos[0]}\n- {zaber_pos[1]}\n- {zaber_pos[2]}.")
      print(f"sensor value {sensor_arr} \n")
  elif args.mode == 'move':
    zaber_pos = calib_manager.get_pos()
    paths = gen_path_cartesian(p_orig= args.start_pos,
                                p_dest= args.end_pos, 
                                N=args.num_path_points,
                                mode='absolute')
    for p in paths:
      calib_manager.goto_pos_mm(p, order=['x','z','y'])
  elif args.mode == 'gather_calib_pts':
    """ 
    since the curved whisker is not regular shape so to better cover most of the length 
    manually define destination points in yaml file to collect data
    """
    calib_manager.goto_origin()

    # get end point
    # both sides
    if (args.path_mode == 'generate'):
      for theta in np.linspace(-np.deg2rad(25) + 3*np.pi/2, np.deg2rad(25) + 3*np.pi/2, 1):
        for phi in np.linspace(np.deg2rad(50), np.deg2rad(70), 2):
          theta = 3*np.pi/2
          # record a data point with no sensor signal

          if phi < np.deg2rad(50):
            r = 18
          elif phi < np.deg2rad(60):
            r = 28
          elif phi < np.deg2rad(70):
            r = 35

          paths = gen_path_spherical(calib_manager.origin_pos, r=r, theta=theta, phi=phi, N=2)
          for p in paths:
            calib_manager.goto_pos_mm(p, order=['x','z','y'])
            data.append({
                'px':p[0],
                'py':p[1],
                'pz':p[2],
                'ind':sensor_arr[0,0],
                })

          calib_manager.reset_to_origin()
        pd.DataFrame.from_dict(data).to_csv(f'data/optic_fiber/sensor{args.sensor_num}_tip_calib_{month_now}_{day_now}_{time_now}.csv')
          
    elif (args.path_mode == 'yaml'):
    # TODO: need to repick up points in yaml since traj will be different for fiber optic whisker array
      with open('tip_calib_pts.yaml', 'r') as f:
        yaml_data = yaml.safe_load(f)

      dest_pts = yaml_data['path_pt_dest']
      # do a reverse pass
      for i, dp in enumerate(dest_pts[::-1]):
        paths = gen_path_cartesian(p_orig=calib_manager.origin_pos, p_dest= np.array(dp), N=7)
        for p in paths:
          calib_manager.goto_pos_mm(p, order=['x','z','y'])
          data.append({
              'px':p[0],
              'py':p[1],
              'pz':p[2],
              'ind':sensor_arr[0,0],
              })
        calib_manager.reset_to_origin()
        pd.DataFrame.from_dict(data).to_csv(f'data/optic_fiber/sensor{args.sensor_num}_tip_calib_{month_now}_{day_now}.csv')

      # do a forward pass
      for i, dp in enumerate(dest_pts):
        paths = gen_path_cartesian(p_orig=calib_manager.origin_pos, p_dest= np.array(dp), N=7)
        for p in paths:
          calib_manager.goto_pos_mm(p, order=['x','z','y'])
          data.append({
              'px':p[0],
              'py':p[1],
              'pz':p[2],
              'sx':sensor_arr[0,0],
              'sy':sensor_arr[0,1],
              'sz':sensor_arr[0,2],
              })
        calib_manager.reset_to_origin()

        pd.DataFrame.from_dict(data).to_csv(f'data/curved_tip/sensor{args.sensor_num}_tip_calib_{month_now}_{day_now}.csv')
  elif args.mode == 'sweep_objects_diff_sensor':
    # Parameters for sweeping different objects with different sensors
    # uncomment the one that corresponds
    # origin position is set at the intersection point of the tapes
    # sensor relative position is set relative to the origin position
    sensor_params = args.sensor_params
    sensor_params[args.sensor_type]
    
    # offset for whisker sensor
    offset_whisker = np.array([74.47, 3., 0])
    # offset for rock
    offset_whisker_rock = np.array([-4.5, 6.0,.0])
    offset_other_rock = np.array([-6, -6.0,.0])
    # mapping of z 123 to 12.733067625
    # mapping of z 84.7 to 50
    
    params = args.object_params[args.object_type]

    # set the reset position
    calib_manager.origin_pos = params['start_position'] - sensor_params['relative_pos']
    # offset reset position to somewhere farther from the object
    calib_manager.reset_pos = params['start_position'] - np.array([0, 40, 0])

    # go to start position
    calib_manager.goto_origin()

    # get initial position as reference point
    init_pos = calib_manager.get_pos()

    data = []

    # angles chosen such that paths are parallel to surface and sweep passed object surface
    phi = np.pi/2
    theta = 3*np.pi/2

    start_pos = init_pos - np.array([0, 0, 0])
    reset_pos = init_pos - np.array([0, 40, 0])

    paths = gen_path_cartesian(p_orig= start_pos,
                                p_dest= start_pos - params['travel_length']*np.array([1,0,0]),
                                N=300,
                                mode='absolute')
    
    i = 0
    calib_manager.goto_pos_mm(start_pos - params['travel_length']*np.array([1,0,0]), order=['z','x','y'], wait=False)
    print("Start scanning object")
    time_init = time.time()
    while True:
      if time.time() - time_init > 20:
        break
      # p = calib_manager.get_pos()
      px = calib_manager.get_sx_pos()
      if px != -1:
        # print("px",px)
        data.append({
            'px': px,
            'ind':sensor_arr[0,0],
            })
    reset_pos = calib_manager.get_pos()
    reset_pos = reset_pos - np.array([0, 50, 0])
    calib_manager.goto_pos_mm(reset_pos, order=['y','x','z'], wait=False)
    pd.DataFrame.from_dict(data).to_csv(f'data/{args.sensor_type}/{args.object_type}_{month_now}_{day_now}_{time_now}.csv')     
  
  elif args.mode == 'sweep_underwater_whisker':
    # Parameters for sweeping different objects with different sensors
    # uncomment the one that corresponds
    # origin position is set at the intersection point of the tapes
    # sensor relative position is set relative to the origin position
    sensor_params = args.sensor_params
    sensor_params = sensor_params[args.sensor_type]
    
    params = args.object_params[args.object_type]

    # set the reset position
    calib_manager.origin_pos = np.array(params['start_position']) - np.array(sensor_params['relative_pos'])
    # offset reset position to somewhere farther from the object
    calib_manager.reset_pos = np.array(params['reset_position'])

    # go to start position
    calib_manager.goto_origin()

    # get initial position as reference point

    data = []
    calib_manager.goto_pos_mm(np.array(params['start_position']) - params['travel_length']*np.array([1,0,0]), order=['z','x','y'], wait=False)
    print("Start scanning object")
    time_init = time.time()
    while True:
      if time.time() - time_init > 20:
        break
      # p = calib_manager.get_pos()
      px = calib_manager.get_sx_pos()
      if px != -1:
        # print("px",px)
        data.append({
            'px': px,
            'ind':sensor_arr[0,0],
            })
    for _ in range(200):
      reset_pos = calib_manager.get_pos()
    print(reset_pos)
    reset_pos = reset_pos - np.array([0, 30, 0])
    calib_manager.goto_pos_mm(reset_pos, order=['y','x','z'], wait=False)
    pd.DataFrame.from_dict(data).to_csv(f'data/{args.sensor_type}/{args.object_type}_{month_now}_{day_now}_{time_now}.csv')     
  
  elif args.mode == 'move_tip_sweep_objects_diff_sensor':
    # Parameters for sweeping different objects with different sensors
    # uncomment the one that corresponds
    # origin position is set at the intersection point of the tapes
    # sensor relative position is set relative to the origin position
    # - 49.360978875
    # - 30.36951
    # - 38.508574875.

    # - 0.246649875
    # - 30.36951
    # - 38.508574875.

    # offset for whisker sensor
    # offset_whisker = np.array([74.47, 3., 0])
    # # offset for rock
    # offset_whisker_rock = np.array([-4.5, 6.0,.0])
    # offset_other_rock = np.array([-6, -6.0,.0])
    # # mapping of z 123 to 12.733067625
    # # mapping of z 84.7 to 50
    
    # params = args.object_params[args.object_type]

    # # set the reset position
    # calib_manager.origin_pos = params['start_position'] - sensor_params['relative_pos']
    # # offset reset position to somewhere farther from the object
    # calib_manager.reset_pos = params['start_position'] - np.array([0, 40, 0])

    # go to start position
    # get initial position as reference point
    print("Start scanning object")
    calib_manager.goto_pos_mm(np.array([49.360978875, 30.36951, 38.508574875]), order=['x','z','y'])
    points = np.linspace(49.360978875, 0.246649875, 100)
    for p in points:
        # print("px",px)
        calib_manager.goto_pos_mm(np.array([p, 30.36951, 38.508574875]), order=['x','z','y'])
        data.append({
            'px': p,
            'ind':sensor_arr[0,0],
            })
    reset_pos = calib_manager.get_pos()
    reset_pos = reset_pos - np.array([50, 0, 0])
    calib_manager.goto_pos_mm(reset_pos, order=['x','z','y'], wait=False)
    pd.DataFrame.from_dict(data).to_csv(f'data/calibration/testing_{month_now}_{day_now}.csv')     
  
  elif args.mode == 'real2sim_calib':
    # Parameters for sweeping different objects with different sensors
    # uncomment the one that corresponds
    # origin position is set at the intersection point of the tapes
    # sensor relative position is set relative to the origin position
    path_pd = pd.read_csv(args.path_points)
    calib_manager.goto_origin()
    time.sleep(5)
    # read path points fromm csv
    
    # get stpx, stpy, stpz from pd
    paths = np.array(path_pd[['stpx','stpy','stpz']])
    paths[:,1] = -paths[:,1]
    paths[:,2] = -paths[:,2]
    paths = paths * 1000 + calib_manager.origin_pos
    point_per_path = 10
    for i in range(len(paths)):
      if i%point_per_path == 0:
        calib_manager.reset_to_origin()
        time.sleep(10)
      p = paths[i]
      calib_manager.goto_pos_mm(p, order=['x','z','y'])
      time.sleep(10)
      print(sensor_arr[0,0])
      data.append({
          'px':p[0],
          'py':p[1],
          'pz':p[2],
          'ind':sensor_arr[0,0],
          })
      time.sleep(10)
    
    calib_manager.goto_reset_all()
      
    pd.DataFrame.from_dict(data).to_csv(f'data/calibration/sensor{args.sensor_num}_{month_now}_{day_now}.csv')

      

if __name__ == '__main__':
  main()