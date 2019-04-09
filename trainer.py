import os
import json
import torch
import logging
import logging.config
from datetime import datetime
from torch.utils.data import DataLoader

from transformer.model import Transformer
from training.optimizer import NoamOpt
from training.loss import LabelSmoothingLoss, CrossEntropyLoss
from training.statistics_collector import StatisticsCollector
from dataset.copy_task import CopyTaskDataset


class Trainer(object):
    """
    Represents a worker taking care of the training of an instance of the ``Transformer`` model.

    """

    def __init__(self, params):
        """
        Constructor of the Trainer.
        Sets up the following:
            - Device available (e.g. if CUDA is present)
            - Initialize the model, dataset, loss, optimizer
            - log statistics (epoch, elapsed time, BLEU score etc.)
        """

        # if CUDA available, moves computations to GPU
        if torch.cuda.is_available():
            device = torch.device('cuda')
        else:
            device = torch.device('cpu')

        # configure all logging
        self.configure_logging(training_problem_name="copy_task", params=params)

        # Initialize TensorBoard and statistics collection.
        self.initialize_statistics_collection()
        self.initialize_tensorboard()

        # instantiate model
        self.model = Transformer(params["model"]).to(device)

        # instantiate loss
        if "smoothing" in params["training"]:
            self.loss_fn = LabelSmoothingLoss(size=params["model"]["tgt_vocab_size"],
                                              padding_token=params["dataset"]["pad_token"],
                                              smoothing=params["training"]["smoothing"])
            self.logger.info("Using LabelSmoothingLoss with smoothing={}.".format(params["training"]["smoothing"]))
        else:
            self.loss_fn = CrossEntropyLoss(pad_token=params["dataset"]["pad_token"])
            self.logger.info("Using CrossEntropyLoss.")

        # instantiate optimizer
        self.optimizer = NoamOpt(model=self.model,
                                 model_size=params["model"]["d_model"],
                                 lr=params["optim"]["lr"],
                                 betas=params["optim"]["betas"],
                                 eps=params["optim"]["eps"],
                                 factor=params["optim"]["factor"],
                                 warmup=params["optim"]["warmup"])

        # get number of epochs and related hyper parameters
        self.epochs = params["training"]["epochs"]

        # initialize training Dataset class
        self.training_dataset = CopyTaskDataset(max_int=params["dataset"]["training"]["max_int"],
                                                max_seq_length=params["dataset"]["training"]["max_seq_length"],
                                                size=params["dataset"]["training"]["size"])

        # initialize DataLoader
        self.training_dataloader = DataLoader(dataset=self.training_dataset,
                                              batch_size=params["training"]["batch_size"],
                                              shuffle=False, num_workers=0,
                                              collate_fn=self.training_dataset.collate)

        # initialize validation Dataset class
        self.validation_dataset = CopyTaskDataset(max_int=params["dataset"]["validation"]["max_int"],
                                                  max_seq_length=params["dataset"]["validation"]["max_seq_length"],
                                                  size=params["dataset"]["validation"]["size"])

        # initialize Validation DataLoader
        self.validation_dataloader = DataLoader(dataset=self.validation_dataset,
                                                batch_size=len(self.validation_dataset),
                                                shuffle=False, num_workers=0,
                                                collate_fn=self.validation_dataset.collate)

        self.logger.info('Experiment setup done.')

    def train(self):
        """
        Main training loop.

            - Trains the Transformer model on the specified dataset for a given number of epochs
            - Logs statistics to logger for every batch per epoch

        """
        # Reset the counter.
        episode = -1

        for epoch in range(self.epochs):

            # Empty the statistics collector.
            self.training_stat_col.empty()

            # collect epoch index
            self.training_stat_col['epoch'] = epoch + 1
            self.validation_stat_col['epoch'] = epoch + 1

            self.model.train()
            for i, batch in enumerate(self.training_dataloader):

                # "Move on" to the next episode.
                episode += 1

                # 1. reset all gradients
                self.optimizer.zero_grad()

                # Convert batch to CUDA.
                if torch.cuda.is_available():
                    batch.cuda()

                # 2. Perform forward calculation.
                logits = self.model(batch.src, batch.src_mask, batch.trg, batch.trg_mask)

                # 3. Evaluate loss function.
                loss = self.loss_fn(logits, batch.trg_y)

                # 4. Backward gradient flow.
                loss.backward()

                # 4.1. Export to csv - at every step.
                # collect loss, episode
                self.training_stat_col['loss'] = loss.item()
                self.training_stat_col['episode'] = episode
                self.training_stat_col.export_to_csv()

                # 4.2. Log "elementary" statistics - episode and loss.
                self.logger.info(self.training_stat_col.export_to_string())

                # 4.3 Exports to tensorboard
                self.training_stat_col.export_to_tensorboard()

                # 5. Perform optimization.
                self.optimizer.step()

            # save model at end of each epoch
            self.model.save(self.model_dir, epoch, loss.item())
            self.logger.info("Model exported to checkpoint.")

            # validate the model on the validation set
            self.model.eval()
            for batch in self.validation_dataloader:

                # Convert batch to CUDA.
                if torch.cuda.is_available():
                    batch.cuda()

                # 1. Perform forward calculation.
                logits = self.model(batch.src, batch.src_mask, batch.trg, batch.trg_mask)

                # 2. Evaluate loss function.
                loss = self.loss_fn(logits, batch.trg_y)

                # 3.1. Export to csv - at every step.
                # collect loss, episode
                self.validation_stat_col['loss'] = loss.item()
                self.validation_stat_col['episode'] = episode
                self.validation_stat_col.export_to_csv()

                # 3.2 Log "elementary" statistics - episode and loss.
                self.logger.info(self.training_stat_col.export_to_string('[Validation]'))

                # 3.3 Export to Tensorboard
                self.validation_stat_col.export_to_tensorboard()

        # training done, end statistics collection
        self.finalize_statistics_collection()
        self.finalize_tensorboard()

    def configure_logging(self, training_problem_name: str, params: dict) -> None:
        """
        Takes care of the initialization of logging-related objects:

            - Sets up a logger with a specific configuration,
            - Sets up a logging directory
            - sets up a logging file in the log directory
            - Sets up a folder to store trained models

        :param training_problem_name: Name of the dataset / training task (e.g. "copy task", "IWLST"). Used for the logging
        folder name.
        """
        # instantiate logger
        # Load the default logger configuration.
        logger_config = {'version': 1,
                         'disable_existing_loggers': False,
                         'formatters': {
                             'simple': {
                                 'format': '[%(asctime)s] - %(levelname)s - %(name)s >>> %(message)s',
                                 'datefmt': '%Y-%m-%d %H:%M:%S'}},
                         'handlers': {
                             'console': {
                                 'class': 'logging.StreamHandler',
                                 'level': 'INFO',
                                 'formatter': 'simple',
                                 'stream': 'ext://sys.stdout'}},
                         'root': {'level': 'DEBUG',
                                  'handlers': ['console']}}

        logging.config.dictConfig(logger_config)

        # Create the Logger, set its label and logging level.
        self.logger = logging.getLogger(name='Trainer')

        # Prepare the output path for logging
        time_str = '{0:%Y%m%d_%H%M%S}'.format(datetime.now())
        self.log_dir = 'experiments/' + training_problem_name + '/' + time_str + '/'

        os.makedirs(self.log_dir, exist_ok=False)
        self.logger.info('Folder {} created.'.format(self.log_dir))

        # Set log dir and add the handler for the logfile to the logger.
        self.log_file = self.log_dir + 'training.log'
        self.add_file_handler_to_logger(self.log_file)

        self.logger.info('Log File {} created.'.format(self.log_file))

        # Models dir: to store the trained models.
        self.model_dir = self.log_dir + 'models/'
        os.makedirs(self.model_dir, exist_ok=False)

        self.logger.info('Model folder {} created.'.format(self.model_dir))

        # save the configuration as a json file in the experiments dir
        with open(self.log_dir + 'params.json', 'w') as fp:
            json.dump(params, fp)

        self.logger.info('Configuration saved to {}.'.format(self.log_dir + 'params.json'))

    def add_file_handler_to_logger(self, logfile: str) -> None:
        """
        Add a ``logging.FileHandler`` to the logger.

        Specifies a ``logging.Formatter``:
            >>> logging.Formatter(fmt='[%(asctime)s] - %(levelname)s - %(name)s >>> %(message)s',
            >>>                   datefmt='%Y-%m-%d %H:%M:%S')

        :param logfile: File used by the ``FileHandler``.

        """
        # create file handler which logs even DEBUG messages
        fh = logging.FileHandler(logfile)

        # set logging level for this file
        fh.setLevel(logging.DEBUG)

        # create formatter and add it to the handlers
        formatter = logging.Formatter(fmt='[%(asctime)s] - %(levelname)s - %(name)s >>> %(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        fh.setFormatter(formatter)

        # add the handler to the logger
        self.logger.addHandler(fh)

    def initialize_statistics_collection(self) -> None:
        """
        Initializes 2 :py:class:`StatisticsCollector` to track statistics for training and validation.

        Adds some default statistics, such as the loss, episode idx and the epoch idx.

        Also creates the output files (csv).
        """
        # TRAINING.
        # Create statistics collector for training.
        self.training_stat_col = StatisticsCollector()

        # add default statistics
        self.training_stat_col.add_statistic('epoch', '{:02d}')
        self.training_stat_col.add_statistic('loss', '{:12.10f}')
        self.training_stat_col.add_statistic('episode', '{:06d}')

        # Create the csv file to store the training statistics.
        self.training_batch_stats_file = self.training_stat_col.initialize_csv_file(self.log_dir,
                                                                                    'training_statistics.csv')

        # VALIDATION.
        # Create statistics collector for validation.
        self.validation_stat_col = StatisticsCollector()

        # add default statistics
        self.validation_stat_col.add_statistic('epoch', '{:02d}')
        self.validation_stat_col.add_statistic('loss', '{:12.10f}')
        self.validation_stat_col.add_statistic('episode', '{:06d}')

        # Create the csv file to store the validation statistics.
        self.validation_batch_stats_file = self.validation_stat_col.initialize_csv_file(self.log_dir,
                                                                                        'validation_statistics.csv')

    def finalize_statistics_collection(self) -> None:
        """
        Finalizes the statistics collection by closing the csv files.
        """
        # Close all files.
        self.training_batch_stats_file.close()
        self.validation_batch_stats_file.close()

    def initialize_tensorboard(self) -> None:
        """
        Initializes the TensorBoard writers, and log directories.
        """
        from tensorboardX import SummaryWriter

        self.training_writer = SummaryWriter(self.log_dir + '/training')
        self.training_stat_col.initialize_tensorboard(self.training_writer)

        self.validation_writer = SummaryWriter(self.log_dir + '/validation')
        self.validation_stat_col.initialize_tensorboard(self.validation_writer)

    def finalize_tensorboard(self):
        """
        Finalizes the operation of TensorBoard writers by closing them.
        """
        # Close the TensorBoard writers.
        self.training_writer.close()
        self.validation_writer.close()


if __name__ == '__main__':

    params = {
        "training": {
                "epochs": 10,
                "batch_size": 30,
                "smoothing": 0.0,
        },

        "optim": {
            "lr": 0.,
            "betas": (0.9, 0.98),
            "eps": 1e-9,
            "factor": 1,
            "warmup": 400

        },

        "dataset": {
            "pad_token": 0,

            'training': {
                    'max_int': 11,
                    'max_seq_length': 10,
                    'size': 30*20},

            'validation': {
                'max_int': 11,
                'max_seq_length': 10,
                'size': 10}

        },

        "model": {
                'd_model': 512,
                'src_vocab_size': 11,
                'tgt_vocab_size': 11,

                'N': 2,
                'dropout': 0.1,

                'attention': {'n_head': 8,
                              'd_k': 64,
                              'd_v': 64,
                              'dropout': 0.1},

                'feed-forward': {'d_ff': 2048,
                                 'dropout': 0.1}
        }
    }
    trainer = Trainer(params)
    trainer.train()

    src = torch.Tensor([[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]])
    src_mask = torch.ones(1, 1, 10)

    predictions = trainer.model.greedy_decode(src, src_mask, start_symbol=1)
    print(predictions)