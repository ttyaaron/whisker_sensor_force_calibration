#!/usr/bin/env python3
"""
Example usage of the FBG Animation Generator

This script demonstrates different ways to use the anime.py program
"""

import os
from pathlib import Path
import sys

if __package__ is None or __package__ == "":
    current_dir = Path(__file__).resolve().parent
    src_root = current_dir.parents[3]
    if str(src_root) not in sys.path:
        sys.path.append(str(src_root))
    from whisker_flow.fbg.utils.anime import FBGAnimator
else:
    from .anime import FBGAnimator

def example_basic_usage():
    """Basic usage example"""
    print("=== Basic Usage Example ===")
    
    # Initialize animator with default settings
    animator = FBGAnimator("./anime_config.yaml")

    # Load data (first 10 seconds)
    data_file = "./data/whisker_20250818-165848.csv"
    animator.load_data(data_file, start_time=227, end_time=243)
    
    # Create animation for FBG1 only
    output_file = animator.create_animation('fbg_2')
    print(f"Created animation: {output_file}")

def example_custom_config():
    """Example with custom configuration"""
    print("\n=== Custom Configuration Example ===")
    
    # Initialize with config file
    animator = FBGAnimator(config_file='anime_config.yaml')
    
    # Override some settings programmatically
    animator.config['animation']['fps'] = 60  # Higher FPS
    animator.config['animation']['speed_factor'] = 2.0  # 2x speed
    animator.config['visualization']['line_width'] = 3  # Thicker line
    animator.config['output']['quality'] = 'high'
    
    # Load data
    data_file = "./data/whisker_20250818-163321.csv"
    animator.load_data(data_file, start_time=5, end_time=15)  # 10 seconds from 5s to 15s
    
    # Create both animations
    results = animator.create_both_animations()
    for sensor, output_file in results.items():
        if output_file:
            print(f"Created {sensor} animation: {output_file}")

def example_time_range():
    """Example focusing on specific time range"""
    print("\n=== Time Range Example ===")
    
    animator = FBGAnimator()
    
    # Customize for shorter window and faster playback
    animator.config['animation']['window_duration'] = 1.0  # 1 second window
    animator.config['animation']['speed_factor'] = 0.5     # Half speed (slow motion)
    animator.config['animation']['fps'] = 30
    
    # Load specific time range
    data_file = "./data/whisker_20250818-163321.csv"
    animator.load_data(data_file, start_time=20, end_time=30)
    
    # Create animation for FBG2
    output_file = animator.create_animation('fbg_2')
    print(f"Created slow motion animation: {output_file}")

def example_visual_customization():
    """Example with visual customization"""
    print("\n=== Visual Customization Example ===")
    
    animator = FBGAnimator()
    
    # Customize appearance
    animator.config['visualization'].update({
        'figure_size': [16, 10],           # Larger figure
        'line_width': 4,                   # Thick line
        'line_color_fbg1': '#FF6B6B',      # Red
        'line_color_fbg2': '#4ECDC4',      # Teal
        'background_color': '#2C3E50',     # Dark background
        'grid': True,
        'grid_alpha': 0.2,
        'title_fontsize': 20,
        'label_fontsize': 14,
    })
    
    # High quality output
    animator.config['output'].update({
        'quality': 'high',
        'bitrate': '4000k',
        'filename_prefix': 'custom_fbg'
    })
    
    # Load data
    data_file = "./data/whisker_20250818-163321.csv"
    animator.load_data(data_file, start_time=0, end_time=5)
    
    # Create both animations
    results = animator.create_both_animations('./custom_animations')
    for sensor, output_file in results.items():
        if output_file:
            print(f"Created custom {sensor} animation: {output_file}")

if __name__ == "__main__":
    # Check if data file exists
    sample_data = "./data/whisker_20250818-163321.csv"
    if not os.path.exists(sample_data):
        print(f"Sample data file not found: {sample_data}")
        print("Please make sure you have data files in the ./data/ directory")
        exit(1)
    
    # Run examples
    # try:
    example_basic_usage()
    #     example_custom_config()
    #     example_time_range()
    #     example_visual_customization()
        
    #     print("\n=== All Examples Complete ===")
    #     print("Check the ./animations/ and ./custom_animations/ directories for output files")
        
    # except Exception as e:
    #     print(f"Error running examples: {e}")
    #     print("Make sure you have the required dependencies installed:")
    #     print("pip install pandas numpy matplotlib pyyaml")
