import os
import time
import argparse
from collections import defaultdict
from multiprocessing.managers import BaseManager, DictProxy

import yaml
from tqdm import tqdm
import numpy as np
import torch
import torch.multiprocessing as mp

from model import DeepCube
from env import make_env
from utils import ReplayBuffer, get_env_config, loss_func, optim_func, scheduler_func,\
    update_params, plot_progress, plot_valid_hist, save_model

def train(cfg, args):
    """
    Train model
    """
    ############################
    # Get train configuration  #
    ############################
    device = torch.device('cuda' if cfg['device']=='cuda' and torch.cuda.is_available() else 'cpu')
    batch_size = cfg['train']['batch_size']
    sample_size = cfg['train']['sample_size']
    learning_rate = cfg['train']['learning_rate']
    epochs = cfg['train']['epochs']
    sample_epoch = cfg['train']['sample_epoch']
    sample_scramble_count = cfg['train']['sample_scramble_count']
    sample_cube_count = cfg['train']['sample_cube_count']
    buffer_size = cfg['train']['buffer_size']
    temperature = cfg['train']['temperature']
    validation_epoch = cfg['train']['validation_epoch']
    num_processes = cfg['train']['num_processes']
    model_path = cfg['train']['model_path']
    progress_path = cfg['train']['progress_path']
    cube_size = cfg['env']['cube_size']
    state_dim, action_dim = get_env_config(cube_size)
    hidden_dim = cfg['model']['hidden_dim']

    ############################
    #      Train settings      #
    ############################
    if num_processes: # Use multi process
        BaseManager.register('defaultdict', defaultdict, DictProxy)
        mgr = BaseManager()
        mgr.start()
        loss_history = mgr.defaultdict(dict)
        valid_history = mgr.defaultdict(dict)

        deepcube = DeepCube(state_dim, action_dim, hidden_dim).to(device)
        if args.resume:
            checkpoint = torch.load(args.resume)
            start_epoch = checkpoint['epoch']+1
            deepcube.load_state_dict(checkpoint['model_state_dict'])
            for idx in range(1, start_epoch):
                loss_history[idx] = {'loss':[0.0]}
            for idx in range(1, ((start_epoch-1)//validation_epoch)+1):
                valid_history[idx*validation_epoch] = {'solve_percentage' : [0.0]*sample_scramble_count}
        deepcube.share_memory() # global model
        optimizer = optim_func(deepcube, learning_rate)
        optimizer.share_memory()
        
    else:
        env = make_env(device, cube_size)
        start_epoch = 1

        criterion_list = loss_func()
        optimizer = optim_func(deepcube, learning_rate)

        replay_buffer = ReplayBuffer(buffer_size, sample_size, per=True)
        loss_history = defaultdict(lambda: {'loss':[]})
        valid_history = defaultdict(lambda: {'solve_percentage':[]})

        if args.resume:
            checkpoint = torch.load(args.resume, map_location = device)
            start_epoch = checkpoint['epoch']+1
            deepcube.load_state_dict(checkpoint['model_state_dict'])

    ############################
    #       train model        #
    ############################
    if num_processes: # Use multi process
        worker_epochs_list = [epochs // num_processes for _ in range(num_processes)]
        for i in range(epochs % num_processes):
            worker_epochs_list[i] += 1
        workers = [mp.Process(target=single_train, args=(worker_idx, worker_epochs_list[worker_idx-1], deepcube, optimizer, valid_history, loss_history, cfg))\
                     for worker_idx in range(1, num_processes+1)]
        [w.start() for w in workers]
        [w.join() for w in workers]

    else: # if num_processes == 0, then train with single machine
        for epoch in tqdm(range(start_epoch, epochs+1)):
            a = time.time()
            if (epoch-1) % sample_epoch == 0: # replay buffer에 random sample저장
                env.get_random_samples(replay_buffer, deepcube, sample_scramble_count, sample_cube_count, temperature)
            loss = update_params(deepcube, replay_buffer, criterion_list, optimizer, batch_size, device, temperature)
            loss_history[epoch]['loss'].append(loss)
            if epoch % validation_epoch == 0:
                validation(deepcube, env, valid_history, epoch, device, cfg)
                plot_valid_hist(valid_history, save_file_path=progress_path, validation_epoch=validation_epoch)
                save_model(deepcube, epoch, optimizer, model_path)
                plot_progress(loss_history, save_file_path=progress_path)
            print(f'{epoch} : Time {time.time()-a}')

def single_train(worker_idx, local_epoch_max, global_deepcube, optimizer, valid_history, loss_history, cfg):
    """
    Function for train on single process

    Args:
        worker_idx: Process index
        local_epoch_max: Train epoch on single process
        global_deepcube: Shared global train model
        optimizer: Torch optimizer for global deepcube parameters
        valid_history: Dictionary for saving validation result
        loss_history: Dictionary for saving loss history
        cfg: config data from yaml file    
    """
    device = torch.device(f'cpu:{worker_idx}')
    torch.set_num_threads(1)
    batch_size = cfg['train']['batch_size']
    sample_size = cfg['train']['sample_size']
    epochs = cfg['train']['epochs']
    sample_epoch = cfg['train']['sample_epoch']
    sample_scramble_count = cfg['train']['sample_scramble_count']
    sample_cube_count = cfg['train']['sample_cube_count']
    buffer_size = cfg['train']['buffer_size']
    temperature = cfg['train']['temperature']
    validation_epoch = cfg['train']['validation_epoch']
    model_path = cfg['train']['model_path']
    progress_path = cfg['train']['progress_path']
    cube_size = cfg['env']['cube_size']
    state_dim, action_dim = get_env_config(cube_size)
    hidden_dim = cfg['model']['hidden_dim']

    global_deepcube = global_deepcube
    deepcube = DeepCube(state_dim, action_dim, hidden_dim).to(device)
    deepcube.load_state_dict(global_deepcube.state_dict())
    env = make_env(device, cube_size)
    local_epoch = 0

    optimizer = optimizer
    criterion_list = loss_func()

    replay_buffer = ReplayBuffer(buffer_size, sample_size)
    valid_history = valid_history
    loss_history = loss_history

    start = time.time()
    while local_epoch < local_epoch_max:
        local_epoch += 1
        if (local_epoch-1) % sample_epoch == 0:
            env.get_random_samples(replay_buffer, global_deepcube, sample_scramble_count, sample_cube_count, temperature)
        deepcube.load_state_dict(global_deepcube.state_dict())
        loss = update_params(deepcube, replay_buffer, criterion_list, optimizer, batch_size, device, temperature, global_deepcube)
        global_epoch = len(loss_history)+1
        loss_history[global_epoch] = {'loss':[loss]}
        print(f"Train progress : {global_epoch} / {epochs}   Loss : {loss}   Time : {(time.time()-start)//60}min {(time.time()-start)%60:.1f}sec")
        if global_epoch % validation_epoch == 0:
            plot_progress(loss_history, save_file_path=progress_path)
            validation(deepcube, env, valid_history, global_epoch, device, cfg)
            plot_valid_hist(valid_history, save_file_path=progress_path, validation_epoch=validation_epoch)
            save_model(global_deepcube, global_epoch, optimizer, model_path)

def validation(model, env, valid_history, epoch, device, cfg):
    """
    Validate model, Solve scrambled cubes with trained model and save video
    Args:
        model: trained DeepCube model
        env: Cube environment
        valid_history: Dictionary to store results
        epoch: Current epoch
        cfg: Which contains validation configuration
    """
    max_timesteps = cfg['validation']['max_timesteps']
    sample_scramble_count = cfg['validation']['sample_scramble_count']
    sample_cube_count = cfg['validation']['sample_cube_count']
    seed = [i*10 for i in range(sample_cube_count)]
    solve_percentage_list = []
    video_path = cfg['train']['video_path']
    for scramble_count in range(1, sample_scramble_count+1):
        solve_count = 0
        for idx in range(1, sample_cube_count+1):
            state, done = env.reset(seed=seed[idx-1], scramble_count=scramble_count), False
            for timestep in range(1, max_timesteps+1):
                with torch.no_grad():
                    state_tensor = torch.tensor(state).float().to(device).detach()
                    action = model.get_action(state_tensor)
                next_state, reward, done, info = env.step(action)
                if done:
                    solve_count += 1
                    break
                state = next_state
        solve_percentage = (solve_count/sample_cube_count) * 100
        solve_percentage_list.append(solve_percentage)
    valid_history[epoch] = {'solve_percentage':solve_percentage_list}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=str, default='./config/config.yaml', help='Path to config file')
    parser.add_argument('--resume', type=str, default='', help='Path to pretrained model file')
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    train(cfg, args)