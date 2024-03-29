import itertools
from argparse import ArgumentParser, Namespace
from multiprocessing.pool import ThreadPool
from typing import Tuple

import numpy as np
import pandas as pd
import scipy.stats as stats
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_squared_error

THREAD_NUM = 6


def read_data(data_path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    df = pd.read_csv(data_path)
    years = df['年份'].to_numpy()
    y = df['高中阶段教育毛入学率'].to_numpy()
    raw: np.ndarray = df.drop('年份', axis=1).to_numpy()
    column_map = df.drop('年份', axis=1).columns.to_numpy()
    return years, raw, y, column_map


def generate_features(raw: np.ndarray, k: int, column_map: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    m, n = raw.shape
    features = np.zeros((m, 3 * (n * k) ** 2))
    feature_map = np.ndarray(3 * (n * k) ** 2, dtype=object)
    for j, (row, col) in enumerate(itertools.product(range(k), range(n))):
        feature_map[j] = '前{}年:{}'.format((3 - row), column_map[col])
    for j, (num, den) in zip(range(k * n, (k * n) ** 2),
                             itertools.permutations(range(k * n), 2)):
        feature_map[j] = '{}/{}'.format(feature_map[num], feature_map[den])
    for j, ele in zip(range((k * n) ** 2, (k * n) ** 2 * 2), range((k * n) ** 2)):
        feature_map[j] = '[{}]** 2'.format(feature_map[ele])
    for j, ele in zip(range((k * n) ** 2 * 2, (k * n) ** 2 * 3), range((k * n) ** 2)):
        feature_map[j] = 'ln[{}]'.format(feature_map[ele])

    for i in range(k, m):
        feature = features[i]
        # Direct copy raw data from the sliding window.
        for j, (row, col) in enumerate(itertools.product(range(i - k, i), range(n))):
            feature[j] = raw[row, col]
        # Divide every possible feature pair
        for j, (num, den) in zip(range(k * n, (k * n) ** 2),
                                 itertools.permutations(feature[:k * n], 2)):
            feature[j] = num / den
        feature[(k * n) ** 2: (k * n) ** 2 * 2] = feature[:(k * n) ** 2] ** 2
        feature[(k * n) ** 2 * 2: (k * n) ** 2 * 3] = np.log(feature[:(k * n) ** 2])

    return features, feature_map


def pearson_selection(features: np.ndarray, y: np.ndarray, k: int, r_min: int, feature_map: np.ndarray)\
        -> Tuple[np.ndarray, np.ndarray]:
    n = features.shape[1]
    r = np.zeros(n)
    for j in range(n):
        r[j] = stats.pearsonr(features[k:, j], y[k:])[0]
    print('-----------Pearson Correlation Coefficient-----------')
    pre = 0
    for cur in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1]:
        in_interval = np.sum((pre <= np.abs(r)) * (np.abs(r) < cur))
        print('{:.1f}\t{:.1f}\t{:.2f}\t{}'.format(pre, cur, in_interval / n, in_interval))
        pre = cur

    correlated_features = features[:, np.abs(r) > r_min]
    feature_map = feature_map[np.abs(r) > r_min]
    return correlated_features, feature_map


def cross_validate(features: np.ndarray, y: np.ndarray, k: int, debug: bool = False):
    m, n = features.shape
    scores = np.zeros((m, 3))
    if debug:
        print('-----------Overall Experiment Result-----------')
        print('R Square\tRMSE_train\tRMSE_test\tGER_pred\tGER_true')
    for test in range(k, m):
        reg = LinearRegression()
        train_features = np.delete(features, test, axis=0)
        train_labels = np.delete(y, test, axis=0)
        reg.fit(train_features, train_labels)
        scores[test, 0] = reg.score(train_features, train_labels)
        scores[test, 1] = mean_squared_error(train_labels, reg.predict(train_features), squared=False)
        scores[test, 2] = mean_squared_error([y[test]], reg.predict([features[test]]), squared=False)
        if debug:
            print('{:.6f}\t{:.6f}\t{:.6f}\t{:.6f}\t{:.3f}'.format(scores[test, 0], scores[test, 1], scores[test, 2],
                                                                  reg.predict([features[test]])[0], y[test]))
    return scores


def forward_search(features: np.ndarray, y: np.ndarray, k: int, f: int, feature_map: np.ndarray) \
        -> Tuple[np.ndarray, np.ndarray]:
    m, n = features.shape
    mask = np.zeros(n, dtype=bool)
    print('-----------Forward Search Result-----------')
    print('RMSE_train\tRMSE_test')
    for i in range(f):
        all_scores = np.zeros((n, m, 3))

        def search_feature(added_feature_pos: int):
            if mask[added_feature_pos]:
                return
            thread_local_mask = mask.copy()
            thread_local_mask[added_feature_pos] = True
            scores = cross_validate(features[:, thread_local_mask], y, k)
            all_scores[added_feature_pos] = scores

        # multi-thread
        pool = ThreadPool(THREAD_NUM)
        pool.map(search_feature, range(n))
        best_score_pos = np.argmax(all_scores[:, :, 0].sum(axis=1))
        mask[best_score_pos] = True
        print('{:.32f}\t{:.32f}'.format(all_scores[best_score_pos, :, 1].mean(),
                                      all_scores[best_score_pos, :, 2].mean()))
        # print(
        #     'Forward search {:2d}/{:2d}, add feature {} \n\t'
        #     'best train set r2 square: {:.3f}, '
        #     'with train / test root mse: {:.3f}/{:.3f}'.format(i + 1, f, feature_map[best_score_pos],
        #                                                        all_scores[best_score_pos, :, 0].mean(),
        #                                                        all_scores[best_score_pos, :, 1].mean(),
        #                                                        all_scores[best_score_pos, :, 2].mean()))
    feature_map = feature_map[mask]
    return features[:, mask], feature_map


def main(args: Namespace):
    years, raw, y, column_map = read_data(args.d)
    generated_features, feature_map = generate_features(raw, args.window_size, column_map)
    selected_features, feature_map = pearson_selection(generated_features, y, args.window_size, args.min_pearson,
                                                       feature_map)
    searched_features, feature_map = forward_search(selected_features, y, args.window_size, args.feature_size,
                                                    feature_map)
    cross_validate(searched_features, y, args.window_size, debug=True)


if __name__ == '__main__':
    parser = ArgumentParser(description='Predict GER (Gross Enrollment Rate) '
                                        'using linear regression.')
    parser.add_argument('-d', metavar='DATA', type=str, default='data.csv',
                        help='The path to the raw data (default: data.csv).')
    parser.add_argument('--window-size', metavar='K', type=int, default=3,
                        help='Size of the time sliding window K (default: 3).')
    parser.add_argument('--min-pearson', metavar='r', type=float, default=0.9,
                        help='Minimum Pearson correlation coefficient between feature and GER (default: 0.9).')
    parser.add_argument('--feature-size', metavar='F', type=int, default=10,
                        help='Size of generated features (default: 20).')
    main(parser.parse_args())
