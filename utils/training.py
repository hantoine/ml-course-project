from glob import glob
import json
import re
import time
import warnings
from os import makedirs
from os.path import join as joinpath, isfile

import pandas as pd
from sklearn.exceptions import ConvergenceWarning

from config import RESULTS_DIR
from utils import compute_loss, compute_metric
# from classification import datasets as clf_ds
# from regression import datasets as reg_ds
from .timeout import set_timeout, TimeoutError


def train_all_models_on_all_datasets(datasets, models, max_training_time=180):
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    evaluate_model_with_timeout = set_timeout(evaluate_model, max_training_time)
    for dataset in datasets:
        print(f'Dataset: {dataset.__name__}')
        train, test = dataset.get()
        for model in models:
            print(f'Model: {model.__name__}')
            try:
                try:
                    tuning_results = get_tuning_results(dataset, model)
                except FileNotFoundError:
                    print('No hyper-parameters saved for this dataset and model')
                    continue

                try:
                    hyperparams = tuning_results['hp']
                    score, train_time, evaluation_time = \
                        evaluate_model_with_timeout(model, dataset, train, test, hyperparams)
                except TimeoutError:
                    print(f'Model training and testing exceeded allowed time ({max_training_time}s)')
                    continue

                save_evaluation_results(dataset, model, tuning_results, score, train_time,
                                        evaluation_time)

                print(f"Results on test set: {dataset.metric}={score:.2f} "
                      f"({tuning_results['score']:.2f} during validation)")
                print(f'Training time: {train_time:.1f}s')
                print(f'Evaluation time: {evaluation_time:.1f}s')
            except MemoryError:
                print('Memory requirements for this model with this dataset are too high')


def evaluate_model(model, dataset, train, test, hyperparams):
    is_cifar_model = model.__name__ == 'Cifar10CustomModel'
    cifar_model_weights_path = joinpath(RESULTS_DIR, 'Cifar10CustomModel-weights.pkl')

    start_time = time.time()
    train_data, test_data = \
        model.prepare_dataset(train, test, dataset.categorical_features)
    estimator = model.build_estimator(hyperparams, train_data)

    # Restore Cifar10CustomModel if weights have been saved
    if is_cifar_model and isfile(cifar_model_weights_path):
            estimator.initialize()
            estimator.load_params(f_params=cifar_model_weights_path)
            train_time = -1
    else:
        X, y, *_ = train_data
        estimator.fit(X, y)
        train_time = time.time() - start_time
        if is_cifar_model:
            estimator.save_params(f_params=cifar_model_weights_path)

    start_time = time.time()
    X_test, y_test = test_data
    metric_value = compute_metric(y_test, estimator.predict(X_test), dataset.metric)
    score = -compute_loss(dataset.metric, [metric_value])
    evaluation_time = time.time() - start_time

    return score, train_time, evaluation_time 

def get_tuning_results(dataset, model):
    tuning_results_dir = joinpath(RESULTS_DIR, dataset.__name__, model.__name__)

    with open(joinpath(tuning_results_dir, 'tuning.json'), 'r', encoding='utf-8') as file:
        prev_results = json.load(file)
        return prev_results

def save_evaluation_results(dataset, model, tuning_results, score, train_time, evaluation_time):
    results_dir = joinpath(RESULTS_DIR, dataset.__name__, model.__name__)
    makedirs(results_dir, exist_ok=True)

    try:
        with open(joinpath(results_dir, 'evaluation.json'), 'r', encoding='utf-8') as file:
            prev_results = json.load(file)
            if dataset.is_metric_maximized:
                better_val_results = tuning_results['score'] > prev_results['val_score']
            else:
                better_val_results = tuning_results['score'] < prev_results['val_score']
    except FileNotFoundError:
        better_val_results = True

    if better_val_results:
        results = {
            'hp': tuning_results['hp'],
            'score': score,
            'tuning_n_trials': tuning_results['n_trials'],
            'val_score': tuning_results['score'],
            'train_time': train_time,
            'evaluation_time': evaluation_time,
            'metric_used': dataset.metric
        }
        with open(joinpath(results_dir, 'evaluation.json'), 'w', encoding='utf-8') as file:
            json.dump(results, file, ensure_ascii=False, indent=4)

def get_results_table():
    result_files = glob('results/*/*/evaluation.json')

    pattern = re.compile(RESULTS_DIR + r'/([a-zA-Z0-9]+)/([a-zA-Z0-9]+)/evaluation.json')
    result_table = []

    for result_file in result_files: # For here
        dataset, model = pattern.match(result_file).groups()
        with open(result_file, 'r') as file:
            results = json.load(file)
        results['hp'] = ','.join([f"{k}={v}" if type(v) in [str, list, type(None)] else f"{k}={v:.2f}"
                                  for k, v in results['hp'].items()])
        results.update({'dataset': dataset, 'model': model})
        result_table.append(results)

    result_table = pd.DataFrame(result_table)
    result_table = result_table[['dataset', 'model', 'score', 'val_score', 'train_time',
                                 'evaluation_time', 'tuning_n_trials', 'hp']]
    return result_table

def get_model_ranking(results):
    results['model_rank'] = results.groupby('dataset')['score'].rank(ascending=False)
    (results[results.dataset == 'DefaultCreditCardDataset']
     .sort_values('score', ascending=False)[['score', 'model_rank', 'model']])
    return results.groupby('model')['model_rank'].mean().sort_values()

def split_resultsby_task(results):
    def dataset_to_task(dataset):
       if hasattr(clf_ds, dataset):
           return 'classification'
       elif hasattr(reg_ds, dataset):
           return 'regression'
       else:
           return 'classifier_interpretability'

    result['task'] = result['dataset'].apply(lambda ds: 'classification' if hasattr(clf_ds, ds) else 'regression')
    clf_results = result[result['task'] == 'classification'].copy()

def print_results():
    results = get_results_table()
    # pd.set_option('display.max_rows', -1)
    # pd.set_option('display.max_colwidth', -1)

    result['task'] = result['dataset'].apply(lambda ds: 'classification' if hasattr(clf_ds, ds) else 'regression')
    clf_results = result[result['task'] == 'classification'].copy()
    # clf_results['metric'] = clf_results['dataset'].apply(lambda ds: getattr(clf_ds, ds).metric)

    print(results)
