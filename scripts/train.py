import torch
import torch.nn as nn
from torch_geometric.data import DataLoader
import numpy as np
import torch.optim as optim
from model import Net
from tools import load_file, save_file
from train_datasets import TrainingDataset, ValidationDataset
import glob
import random
import threading
import os


class TrainModel:
    def __init__(self, training_parameters):
        self.training_parameters = training_parameters
        self.preprocessing()
        # self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        self.device = training_parameters['device']
        self.training_history = np.zeros((0, 5))
        train_dataset = TrainingDataset(root_dir="../data/train_dataset/sample_dir/",
                                        points_per_box=training_parameters['max_points_per_box'],
                                        device=self.device)

        validation_dataset = ValidationDataset(root_dir="../data/train_dataset/sample_dir/",
                                               points_per_box=training_parameters['max_points_per_box'],
                                               device=self.device)

        self.train_loader = DataLoader(train_dataset,
                                       batch_size=training_parameters['batch_size'],
                                       shuffle=True,
                                       num_workers=0)

        self.validation_loader = DataLoader(validation_dataset,
                                            batch_size=training_parameters['batch_size'],
                                            shuffle=True,
                                            num_workers=0)

    def preprocessing(self):
        if self.training_parameters["preprocess_train_datasets"]:
            train_point_cloud_list = glob.glob("../data/train_dataset/*.las")
            print("Preprocessing train_dataset point clouds...")
            for point_cloud_file in train_point_cloud_list:
                print(point_cloud_file)
                point_cloud, headers = load_file(point_cloud_file, headers_of_interest=['x', 'y', 'z', 'label'])
                self.preprocess_point_cloud(point_cloud, "../data/train_dataset/sample_dir/")

        if self.training_parameters["preprocess_test_datasets"]:
            test_point_cloud_list = glob.glob("../data/test_dataset/*.las")
            print("Preprocessing test_dataset point clouds...")
            for point_cloud_file in test_point_cloud_list:
                print(point_cloud_file)
                point_cloud, headers = load_file(point_cloud_file, headers_of_interest=['x', 'y', 'z', 'label'])
                self.preprocess_point_cloud(point_cloud, "../data/test_dataset/sample_dir/")

        if self.training_parameters["preprocess_validation_datasets"]:
            validation_point_cloud_list = glob.glob("../data/validation_dataset/*.las")
            print("Preprocessing validation_dataset point clouds...")
            for point_cloud_file in validation_point_cloud_list:
                print(point_cloud_file)
                point_cloud, headers = load_file(point_cloud_file, headers_of_interest=['x', 'y', 'z', 'label'])
                self.preprocess_point_cloud(point_cloud, "../data/validation_dataset/sample_dir/")

    @staticmethod
    def threaded_boxes(point_cloud, box_size, min_points_per_box, max_points_per_box, path, id_offset, point_divisions):
        box_size = np.array(box_size)
        box_centre_mins = point_divisions - 0.5 * box_size
        box_centre_maxes = point_divisions + 0.5 * box_size
        i = 0
        pds = len(point_divisions)
        while i < pds:
            box = point_cloud
            box = box[np.logical_and(np.logical_and(np.logical_and(box[:, 0] >= box_centre_mins[i, 0],
                                                                   box[:, 0] < box_centre_maxes[i, 0]),
                                                    np.logical_and(box[:, 1] >= box_centre_mins[i, 1],
                                                                   box[:, 1] < box_centre_maxes[i, 1])),
                                     np.logical_and(box[:, 2] >= box_centre_mins[i, 2],
                                                    box[:, 2] < box_centre_maxes[i, 2]))]

            if box.shape[0] > min_points_per_box:
                if box.shape[0] > max_points_per_box:
                    indices = list(range(0, box.shape[0]))
                    random.shuffle(indices)
                    random.shuffle(indices)
                    box = box[indices[:max_points_per_box], :]
                    box = np.asarray(box, dtype='float64')

                box[:, :3] = box[:, :3]
                np.save(path + str(id_offset + i).zfill(7) + '.npy', box)
            i += 1
        return 1

    def preprocess_point_cloud(self, point_cloud, sample_dir):
        print("Pre-processing point cloud...")
        Xmax = np.max(point_cloud[:, 0])
        Xmin = np.min(point_cloud[:, 0])
        Ymax = np.max(point_cloud[:, 1])
        Ymin = np.min(point_cloud[:, 1])
        Zmax = np.max(point_cloud[:, 2])
        Zmin = np.min(point_cloud[:, 2])

        X_range = Xmax - Xmin
        Y_range = Ymax - Ymin
        Z_range = Zmax - Zmin

        num_boxes_x = int(np.ceil(X_range / self.training_parameters['box_dimensions'][0]))
        num_boxes_y = int(np.ceil(Y_range / self.training_parameters['box_dimensions'][1]))
        num_boxes_z = int(np.ceil(Z_range / self.training_parameters['box_dimensions'][2]))

        x_vals = np.linspace(Xmin, Xmin + (num_boxes_x * self.training_parameters['box_dimensions'][0]),
                             int(num_boxes_x / (1 - self.training_parameters['box_overlap'][0])) + 1)
        y_vals = np.linspace(Ymin, Ymin + (num_boxes_y * self.training_parameters['box_dimensions'][1]),
                             int(num_boxes_y / (1 - self.training_parameters['box_overlap'][1])) + 1)
        z_vals = np.linspace(Zmin, Zmin + (num_boxes_z * self.training_parameters['box_dimensions'][2]),
                             int(num_boxes_z / (1 - self.training_parameters['box_overlap'][2])) + 1)

        box_centres = np.vstack(np.meshgrid(x_vals, y_vals, z_vals)).reshape(3, -1).T

        point_divisions = []
        for thread in range(self.training_parameters['num_procs']):
            point_divisions.append([])

        points_to_assign = box_centres

        while points_to_assign.shape[0] > 0:
            for i in range(self.training_parameters['num_procs']):
                point_divisions[i].append(points_to_assign[0, :])
                points_to_assign = points_to_assign[1:]
                if points_to_assign.shape[0] == 0:
                    break
        threads = []
        id_offset = 0
        training_data_list = glob.glob(sample_dir + "*.npy")
        if len(training_data_list) > 0:
            id_offset = np.max([int(os.path.basename(i).split('.')[0]) for i in training_data_list]) + 1

        for thread in range(self.training_parameters['num_procs']):
            for t in range(thread):
                id_offset = id_offset + len(point_divisions[t])
            t = threading.Thread(target=self.threaded_boxes, args=(point_cloud,
                                                                   self.training_parameters['box_dimensions'],
                                                                   self.training_parameters['min_points_per_box'],
                                                                   self.training_parameters['max_points_per_box'],
                                                                   sample_dir,
                                                                   id_offset,
                                                                   point_divisions[thread],))
            threads.append(t)

        for x in threads:
            x.start()

        for x in threads:
            x.join()

    def update_log(self, epoch, epoch_loss, epoch_acc, val_epoch_loss, val_epoch_acc):
        self.training_history = np.vstack((self.training_history, np.array([[epoch, epoch_loss, epoch_acc, val_epoch_loss, val_epoch_acc]])))
        try:
            np.savetxt("../model/training_history.csv", self.training_history)
        except PermissionError:
            print("training_history not saved this epoch, please close training_history.csv to enable saving.")
            try:
                np.savetxt("../model/training_history_permission_error_backup.csv", self.training_history)
            except PermissionError:
                pass

    def run_training(self):
        model = Net(num_classes=4).to(self.device)
        if self.training_parameters['load_existing_model']:
            print('Loading existing model...')
            try:
                model.load_state_dict(torch.load("../model/" + self.training_parameters['model_filename']), strict=False)

            except FileNotFoundError:
                print("File not found, creating new model...")
                torch.save(model.state_dict(), "../model/" + self.training_parameters['model_filename'])

            try:
                self.training_history = np.loadtxt("../model/training_history.csv")
                print("Loaded training history successfully.")
            except OSError:
                pass

        model = model.to(self.device)
        optimizer = optim.Adam(filter(lambda p: p.requires_grad, model.parameters()), lr=self.training_parameters['learning_rate'])
        criterion = nn.CrossEntropyLoss()
        dataloaders = dict(train=self.train_loader, validation=self.validation_loader)
        val_epoch_loss = 0
        val_epoch_acc = 0
        for epoch in range(self.training_parameters['num_epochs']):
            print("=====================================================================")
            print("EPOCH ", epoch)
            # TRAINING
            model.train()
            running_loss = 0.0
            running_acc = 0
            i = 0
            running_point_cloud_vis = np.zeros((0, 5))
            for data in self.train_loader:
                data.pos = data.pos.to(self.device)
                data.y = torch.unsqueeze(data.y, 0).to(self.device)

                outputs = model(data)
                loss = criterion(outputs, data.y)

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                _, preds = torch.max(outputs, 1)
                running_loss += loss.detach().item()
                running_acc += torch.sum(preds == data.y.data).item() / data.y.shape[1]
                running_point_cloud_vis = np.vstack((running_point_cloud_vis, np.hstack((data.pos + np.array([i * 7, 0, 0]), data.y.T, preds.T))))

                if i % 2 == 0:
                    print("Train sample accuracy: ", np.around(running_acc / (i+1), 4), ", Loss: ", np.around(running_loss/(i+1), 4))
                    print(data.pos.shape, data.y.shape, preds.shape)
                    save_file("../data/latest_prediction.las", running_point_cloud_vis, headers_of_interest=['x', 'y', 'z', 'label', 'prediction'])
                i += 1
            epoch_loss = running_loss / len(self.train_loader)
            epoch_acc = running_acc / len(self.train_loader)
            self.update_log(epoch, epoch_loss, epoch_acc, val_epoch_loss, val_epoch_acc)
            print("Train epoch accuracy: ", np.around(epoch_acc, 4), ", Loss: ", np.around(epoch_loss, 4), "\n")

            # VALIDATION
            model.eval()
            running_loss = 0.0
            running_acc = 0
            i = 0
            for data in self.validation_loader:
                data.pos = data.pos.to(self.device)
                data.y = torch.unsqueeze(data.y, 0).to(self.device)

                outputs = model(data)
                loss = criterion(outputs, data.y)

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

                _, preds = torch.max(outputs, 1)
                running_loss += loss.detach().item()
                running_acc += torch.sum(preds == data.y.data).item() / data.y.shape[1]
                if i % 2 == 0:
                    print("Validation sample accuracy: ", np.around(running_acc / (i+1), 4), ", Loss: ", np.around(running_loss/(i+1), 4))

                i += 1
            val_epoch_loss = running_loss / len(self.train_loader)
            val_epoch_acc = running_acc / len(self.train_loader)
            self.update_log(epoch, epoch_loss, epoch_acc, val_epoch_loss, val_epoch_acc)
            print("Validation epoch accuracy: ", np.around(val_epoch_acc, 4), ", Loss: ", np.around(val_epoch_loss, 4))
            print("=====================================================================")
            torch.save(model.state_dict(), "../model/" + self.training_parameters['model_filename'])

    def test_model(self):
        pass


if __name__ == '__main__':
    parameters = dict(preprocess_train_datasets=1,
                      preprocess_validation_datasets=1,
                      preprocess_test_datasets=1,
                      load_existing_model=1,
                      num_epochs=2000,
                      learning_rate=0.000025,
                      fileset=None,
                      input_point_cloud=None,
                      model_filename='modelV2.pth',
                      box_dimensions=np.array([6, 6, 6]),
                      box_overlap=[0.5, 0.5, 0.5],
                      min_points_per_box=1000,
                      max_points_per_box=20000,
                      subsample=False,
                      subsampling_min_spacing=0.025,
                      num_procs=4,
                      batch_size=2,
                      device='cpu')

    run_training = TrainModel(parameters)
    run_training.run_training()
