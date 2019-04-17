#!/usr/bin/env bash
set -euo pipefail

test -x conda || (echo "Please install conda first" && exit 1)

# clone repo
git clone git@gitlab.com:DeepFrench/deep-learning-project.git

cd deep-learning-project/

# create conda env
conda create -n python3-dl python=3 -y

conda activate python3-dl

# install requirements
conda install pytorch torchvision -c pytorch -y
conda install nltk tensorboardx -c conda-forge -y

# torchtext is not available in conda
pip install torchtext
