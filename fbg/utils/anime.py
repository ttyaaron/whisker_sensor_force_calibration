#!/usr/bin/env python3
"""
FBG Data Animation Generator

This script creates animated MP4 videos from FBG sensor data CSV files.
The data should have columns: index, fbg_1, fbg_2
Sampling rate is assumed to be around 1980 Hz.

Features:
- Generate separate animations for FBG1 and FBG2
- Select time range for animation
- Sliding window visualization 
- Customizable visual appearance
- Adjustable video parameters (fps, resolution, etc.)
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.animation as animation
from matplotlib.patches import Rectangle
import argparse
import os
from datetime import datetime
import yaml

class FBGAnimator:
    def __init__(self, config_file=None):
        """
        Initialize the FBG animator with configuration
        
        Args:
            config_file (str): Path to YAML configuration file
        """
        # Default configuration
        self.config = {
            'data': {
                'sampling_rate': 1980,  # Hz
                'csv_file': '',
                'start_time': 0,  # seconds
                'end_time': None,  # seconds, None means end of data
            },
            'animation': {
                'window_duration': 2.0,  # seconds of data to show at once
                'fps': 30,  # frames per second for output video
                'speed_factor': 1.0,  # playback speed multiplier
                'remove_offset': True,  # subtract first value to start from 0
                'normalize_time': True,  # start time axis from 0
            },
            'visualization': {
                'figure_size': (12, 8),
                'dpi': 100,
                'line_width': 2,
                'line_color_fbg1': '#2E86AB',  # Blue
                'line_color_fbg2': '#A23B72',  # Purple
                'background_color': 'white',
                'grid': True,
                'grid_alpha': 0.3,
                'title_fontsize': 16,
                'label_fontsize': 12,
                'tick_fontsize': 10,
            },
            'output': {
                'output_dir': './animations',
                'filename_prefix': 'fbg_animation',
                'format': 'mp4',
                'quality': 'high',  # 'low', 'medium', 'high'
                'bitrate': '2000k',
            }
        }
        
        # Load configuration from file if provided
        if config_file and os.path.exists(config_file):
            with open(config_file, 'r') as f:
                user_config = yaml.safe_load(f)
                self._update_config(self.config, user_config)
    
    def _update_config(self, base_config, update_config):
        """Recursively update configuration dictionary"""
        for key, value in update_config.items():
            if key in base_config and isinstance(base_config[key], dict) and isinstance(value, dict):
                self._update_config(base_config[key], value)
            else:
                base_config[key] = value
    
    def load_data(self, csv_file, start_time=None, end_time=None):
        """
        Load and preprocess CSV data
        
        Args:
            csv_file (str): Path to CSV file
            start_time (float): Start time in seconds
            end_time (float): End time in seconds
        """
        print(f"Loading data from {csv_file}...")
        
        # Read CSV file
        self.data = pd.read_csv(csv_file, index_col=0)
        
        # Ensure we have the expected columns
        if 'fbg_1' not in self.data.columns or 'fbg_2' not in self.data.columns:
            raise ValueError("CSV file must contain 'fbg_1' and 'fbg_2' columns")
        
        # Create time array based on sampling rate
        sampling_rate = self.config['data']['sampling_rate']
        self.time = np.arange(len(self.data)) / sampling_rate
        
        # Apply time range selection
        if start_time is not None:
            self.config['data']['start_time'] = start_time
        if end_time is not None:
            self.config['data']['end_time'] = end_time
            
        start_idx = int(self.config['data']['start_time'] * sampling_rate)
        end_idx = len(self.data) if self.config['data']['end_time'] is None else int(self.config['data']['end_time'] * sampling_rate)
        end_idx = min(end_idx, len(self.data))
        
        self.data = self.data.iloc[start_idx:end_idx]
        self.time = self.time[start_idx:end_idx]
        
        # Normalize time to start from 0 if enabled and time range is selected
        if (self.config['animation']['normalize_time'] and 
            start_time is not None and start_time > 0):
            self.time_offset = self.time[0]
            self.time = self.time - self.time_offset
            print(f"Time normalized: original range {self.time_offset:.2f}-{self.time_offset + self.time[-1]:.2f}s, now 0-{self.time[-1]:.2f}s")
        else:
            self.time_offset = 0
        
        print(f"Data loaded: {len(self.data)} samples, {self.time[-1]:.2f} seconds")
        
        # Store original filename for output naming
        self.csv_filename = os.path.splitext(os.path.basename(csv_file))[0]
    
    def setup_plot(self, sensor_name):
        """
        Setup matplotlib figure and axis for animation
        
        Args:
            sensor_name (str): 'fbg_1' or 'fbg_2'
        """
        vis_config = self.config['visualization']
        
        # Create figure and axis
        self.fig, self.ax = plt.subplots(figsize=vis_config['figure_size'], dpi=vis_config['dpi'])
        self.fig.patch.set_facecolor(vis_config['background_color'])
        
        # Get sensor data
        self.sensor_data = self.data[sensor_name].values
        
        # Apply offset removal if enabled
        if self.config['animation']['remove_offset']:
            self.offset = self.sensor_data[0]  # Store first value as offset
            self.sensor_data = self.sensor_data - self.offset
            print(f"Offset removed: {self.offset:.6f} (data now starts from 0)")
        else:
            self.offset = 0
        
        # Set color based on sensor
        self.line_color = vis_config['line_color_fbg1'] if sensor_name == 'fbg_1' else vis_config['line_color_fbg2']
        
        # Calculate data range for y-axis
        data_min, data_max = np.min(self.sensor_data), np.max(self.sensor_data)
        data_range = data_max - data_min
        y_margin = data_range * 0.1  # 10% margin
        
        self.y_min = data_min - y_margin
        self.y_max = data_max + y_margin
        
        # Setup plot styling
        self.ax.set_facecolor(vis_config['background_color'])
        self.ax.set_ylim(self.y_min, self.y_max)
        
        # Create y-axis label with offset info
        y_label = f'{sensor_name.upper()} Value'
        if self.config['animation']['remove_offset']:
            y_label += f' (Δ from baseline: {self.offset:.6f})'
        
        self.ax.set_ylabel(y_label, fontsize=vis_config['label_fontsize'])
        
        # Create x-axis label with time offset info
        x_label = 'Time (s)'
        if hasattr(self, 'time_offset') and self.time_offset > 0:
            x_label += f' (normalized from {self.time_offset:.1f}s start)'
        
        self.ax.set_xlabel(x_label, fontsize=vis_config['label_fontsize'])
        self.ax.tick_params(labelsize=vis_config['tick_fontsize'])
        
        if vis_config['grid']:
            self.ax.grid(True, alpha=vis_config['grid_alpha'])
        
        # Initialize empty line
        self.line, = self.ax.plot([], [], color=self.line_color, linewidth=vis_config['line_width'])
        
        # Setup title
        title = f'{sensor_name.upper()} Data Animation - {self.csv_filename}'
        self.ax.set_title(title, fontsize=vis_config['title_fontsize'])
        
        # Animation parameters
        self.window_duration = self.config['animation']['window_duration']
        self.speed_factor = self.config['animation']['speed_factor']
        
        plt.tight_layout()
    
    def animate_frame(self, frame):
        """
        Animation function called for each frame
        
        Args:
            frame (int): Frame number
        """
        # Calculate current time based on frame and speed factor
        fps = self.config['animation']['fps']
        # Start animation from 0 (normalized time)
        current_time = frame * self.speed_factor / fps
        
        # Determine the time window to display
        window_start = max(0, current_time - self.window_duration)
        window_end = current_time
        
        # Update x-axis limits (sliding window)
        self.ax.set_xlim(window_start, window_end)
        
        # Find data indices within the current time window
        time_mask = (self.time >= window_start) & (self.time <= current_time)
        
        if np.any(time_mask):
            # Update line data
            x_data = self.time[time_mask]
            y_data = self.sensor_data[time_mask]
            self.line.set_data(x_data, y_data)
        else:
            # No data to show yet
            self.line.set_data([], [])
        
        return self.line,
    
    def create_animation(self, sensor_name, output_file=None):
        """
        Create and save animation for specified sensor
        
        Args:
            sensor_name (str): 'fbg_1' or 'fbg_2'
            output_file (str): Custom output filename
        """
        print(f"Creating animation for {sensor_name}...")
        
        # Setup plot
        self.setup_plot(sensor_name)
        
        # Calculate animation parameters
        fps = self.config['animation']['fps']
        speed_factor = self.config['animation']['speed_factor']
        # Calculate actual data duration (now normalized to start from 0) plus window duration
        data_duration = self.time[-1] - self.time[0]  # This will be self.time[-1] since time[0] is now 0
        total_time = data_duration + self.window_duration
        total_frames = int(total_time * fps / speed_factor)
        
        # Create animation
        anim = animation.FuncAnimation(
            self.fig, 
            self.animate_frame, 
            frames=total_frames,
            interval=1000/fps,  # milliseconds between frames
            blit=True,
            repeat=False
        )
        
        # Setup output file
        if output_file is None:
            output_config = self.config['output']
            os.makedirs(output_config['output_dir'], exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{output_config['filename_prefix']}_{sensor_name}_{self.csv_filename}_{timestamp}.{output_config['format']}"
            output_file = os.path.join(output_config['output_dir'], filename)
        
        # Save animation
        print(f"Saving animation to {output_file}...")
        
        # Setup writer based on quality setting
        quality_settings = {
            'low': {'bitrate': '500000'},
            'medium': {'bitrate': '1000000'},
            'high': {'bitrate': '2000000'}
        }
        
        quality = self.config['output']['quality']
        bitrate = int(quality_settings.get(quality, {}).get('bitrate', self.config['output']['bitrate']))
        
        writer = animation.FFMpegWriter(
            fps=fps,
            bitrate=bitrate,
            extra_args=['-vcodec', 'libx264']
        )
        
        anim.save(output_file, writer=writer)
        plt.close(self.fig)
        
        print(f"Animation saved successfully: {output_file}")
        return output_file
    
    def create_both_animations(self, output_dir=None):
        """
        Create animations for both FBG sensors
        
        Args:
            output_dir (str): Custom output directory
        """
        if output_dir:
            self.config['output']['output_dir'] = output_dir
        
        results = {}
        for sensor in ['fbg_1', 'fbg_2']:
            try:
                output_file = self.create_animation(sensor)
                results[sensor] = output_file
            except Exception as e:
                print(f"Error creating animation for {sensor}: {e}")
                results[sensor] = None
        
        return results

def create_default_config(config_file='anime_config.yaml'):
    """Create a default configuration file"""
    default_config = {
        'data': {
            'sampling_rate': 1980,
            'start_time': 0,
            'end_time': None,
        },
        'animation': {
            'window_duration': 2.0,
            'fps': 30,
            'speed_factor': 1.0,
            'remove_offset': True,
            'normalize_time': True,
        },
        'visualization': {
            'figure_size': [12, 8],
            'dpi': 100,
            'line_width': 2,
            'line_color_fbg1': '#2E86AB',
            'line_color_fbg2': '#A23B72',
            'background_color': 'white',
            'grid': True,
            'grid_alpha': 0.3,
            'title_fontsize': 16,
            'label_fontsize': 12,
            'tick_fontsize': 10,
        },
        'output': {
            'output_dir': './animations',
            'filename_prefix': 'fbg_animation',
            'format': 'mp4',
            'quality': 'high',
            'bitrate': '2000k',
        }
    }
    
    with open(config_file, 'w') as f:
        yaml.dump(default_config, f, default_flow_style=False, indent=2)
    
    print(f"Default configuration saved to {config_file}")

def main():
    parser = argparse.ArgumentParser(description='Generate FBG sensor data animations')
    parser.add_argument('csv_file', help='Path to CSV data file')
    parser.add_argument('--config', '-c', help='Configuration YAML file')
    parser.add_argument('--start', '-s', type=float, help='Start time in seconds')
    parser.add_argument('--end', '-e', type=float, help='End time in seconds')
    parser.add_argument('--sensor', choices=['fbg_1', 'fbg_2', 'both'], default='both',
                       help='Which sensor to animate')
    parser.add_argument('--output', '-o', help='Output directory')
    parser.add_argument('--fps', type=int, help='Frames per second')
    parser.add_argument('--speed', type=float, help='Animation speed factor')
    parser.add_argument('--window', type=float, help='Time window duration in seconds')
    parser.add_argument('--no-offset-removal', action='store_true', 
                       help='Keep original data values (don\'t subtract baseline)')
    parser.add_argument('--no-time-normalization', action='store_true', 
                       help='Keep original time values (don\'t start from 0)')
    parser.add_argument('--create-config', action='store_true', 
                       help='Create default configuration file')
    
    args = parser.parse_args()
    
    if args.create_config:
        create_default_config()
        return
    
    # Initialize animator
    animator = FBGAnimator(args.config)
    
    # Override config with command line arguments
    if args.fps:
        animator.config['animation']['fps'] = args.fps
    if args.speed:
        animator.config['animation']['speed_factor'] = args.speed
    if args.window:
        animator.config['animation']['window_duration'] = args.window
    if args.output:
        animator.config['output']['output_dir'] = args.output
    if args.no_offset_removal:
        animator.config['animation']['remove_offset'] = False
    if args.no_time_normalization:
        animator.config['animation']['normalize_time'] = False
    
    # Load data
    animator.load_data(args.csv_file, args.start, args.end)
    
    # Create animations
    if args.sensor == 'both':
        results = animator.create_both_animations()
        print("Animation generation complete!")
        for sensor, output_file in results.items():
            if output_file:
                print(f"  {sensor}: {output_file}")
    else:
        output_file = animator.create_animation(args.sensor)
        print(f"Animation complete: {output_file}")

if __name__ == "__main__":
    main()
