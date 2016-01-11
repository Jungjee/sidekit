# -*- coding: utf-8 -*-
#
# This file is part of SIDEKIT.
#
# SIDEKIT is a python package for speaker verification.
# Home page: http://www-lium.univ-lemans.fr/sidekit/
#
# SIDEKIT is a python package for speaker verification.
# Home page: http://www-lium.univ-lemans.fr/sidekit/
#    
# SIDEKIT is free software: you can redistribute it and/or modify
# it under the terms of the GNU LLesser General Public License as 
# published by the Free Software Foundation, either version 3 of the License, 
# or (at your option) any later version.
#
# SIDEKIT is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with SIDEKIT.  If not, see <http://www.gnu.org/licenses/>.

"""
Copyright 2014-2015 Anthony Larcher

:mod:`theano_utils` provides utilities to facilitate the work with SIDEKIT
and THEANO.
"""

__license__ = "LGPL"
__author__ = "Anthony Larcher"
__copyright__ = "Copyright 2015-2016 Anthony Larcher"
__license__ = "LGPL"
__maintainer__ = "Anthony Larcher"
__email__ = "anthony.larcher@univ-lemans.fr"
__status__ = "Production"
__docformat__ = 'reStructuredText'

import numpy as np
import scipy as sp
import pickle
import gzip
import os
import time
import random
import sys
import logging
import errno

# warning, only works in python 3
if sys.version_info[0] >= 3:
    from concurrent import futures

import sidekit.frontend


import theano, theano.tensor as T
os.environ['THEANO_FLAGS']='mode=FAST_RUN,device=gpu,floatX=float32'



def segment_mean_std_spro4(input):
    filename, start, stop, left_context, right_context = input
    feat = sidekit.frontend.features.get_context(
                        sidekit.frontend.io.read_spro4_segment(filename, 
                                                               start=start, 
                                                               end=stop), 
                                                               left_ctx=left_context, 
                                                               right_ctx=right_context, 
                                                               hamming=False)
    return feat.shape[0], feat.sum(axis=0), np.sum(feat**2, axis=0)


def segment_mean_std_htk(input):
    filename, start, stop, left_context, right_context = input
    feat = sidekit.frontend.features.get_context(
                        sidekit.frontend.io.read_htk_segment(filename, 
                                                               start=start, 
                                                               end=stop), 
                                                               left_ctx=left_context, 
                                                               right_ctx=right_context, 
                                                               hamming=False)
    return feat.shape[0], feat.sum(axis=0), np.sum(feat**2, axis=0)


if sys.version_info[0] >= 3:
    def mean_std_many(file_format, feature_size, seg_list, 
                  left_context, right_context):
    
        inputs = [(seg[0], 
               seg[1] - left_context, 
               seg[2] + right_context, 
               left_context, right_context) for seg in seg_list]    
   
        MAX_WORKERS = 20 
        if file_format == 'spro4':
            workers = min(MAX_WORKERS, len(seg_list))
            with futures.ProcessPoolExecutor(workers) as executor:
                res = executor.map(segment_mean_std_spro4, sorted(inputs))
        elif file_format == 'htk':
            workers = min(MAX_WORKERS, len(file_list))
            with futures.ProcessPoolExecutor(workers) as executor:
                res = executor.map(segment_mean_std_htk, sorted(inputs))        
        
        total_N = 0
        total_F = np.zeros(feature_size)
        total_S = np.zeros(feature_size)
        for N, F, S in res:
            total_N += N
            total_F += F
            total_S += S
        return total_N, total_F / total_N, total_S / total_N


def get_params(params_):
    return {p.name: p.get_value() for p in params_}


def set_params(params_, param_dict):
    for p_ in params_: p_.set_value(param_dict[p_.name])





class FForwardNetwork():
        
    def __init__(self, filename=None,
                 input_size=0,
                 input_mean=np.empty(0),
                 input_std=np.empty(0), 
                 hidden_layer_sizes=(), 
                 layers_activations=(), 
                 nclasses=0
                 ):
        if filename is not None:
            # Load DNN parameters
            self.params = np.load(filename)
        
            """ AJOUTER  DES VERIFICATIONS SUR LE CONTENU DU DICTIONNAIRE DE PARAMETRES"""
        
        else:  # initialize a NN with given sizes of layers and activation functions
            assert len(layers_activations) == len(hidden_layer_sizes) + 1, \
                "Mismatch between number of hidden layers and activation functions"

            sizes = (input_size,) + tuple(hidden_layer_sizes) + (nclasses,)
        
            self.params = {"input_mean": input_mean.astype(T.config.floatX), 
                      "input_std": input_std.astype(T.config.floatX),
                      "activation_functions": layers_activations,
                      "b{}".format(len(sizes)-1): np.zeros(sizes[-1]).astype(T.config.floatX),
                      "hidden_layer_sizes": hidden_layer_sizes
                    }
                    
            for ii in range(1,len(sizes)):   
                self.params["W{}".format(ii)] = np.random.randn(
                            sizes[ii-1],
                            sizes[ii]).astype(T.config.floatX) * 0.1 
                self.params["b{}".format(ii)] = np.random.random(sizes[ii]).astype(T.config.floatX) / 5.0 - 4.1

        
    def instantiate_network(self):
        """ Create Theano variables and initialize the weights and biases 
        of the neural network
        Create the different funtions required to train the NN
        """ 
        
        # Define the variable for inputs
        X_ = T.matrix("X")
        
        # Define variables for mean and standard deviation of the input
        mean_ = theano.shared(self.params['input_mean'], name='input_mean')
        std_  = theano.shared(self.params['input_std'], name='input_std')
        
        # Define the variable for standardized inputs
        Y_ = (X_ - mean_) / std_
        
        # Get the list of activation functions for each layer
        activation_functions = self.params["activation_functions"]
        
        # Get the number of hidden layers from the length of the dictionnary
        n_hidden_layers = len(self.params) / 2 - 3

        # Define list of variables 
        params_ = [mean_, std_]    
    
        # For each layer, initialized the weights and biases
        for ii, f in enumerate(self.params["activation_functions"]):
            W_name = "W{}".format(ii + 1)
            b_name = "b{}".format(ii + 1)
            W_ = theano.shared(self.params[W_name], name=W_name)
            b_ = theano.shared(self.params[b_name], name=b_name)
            if f is None:
                Y_ = Y_.dot(W_) + b_
            else:
                Y_ = f(Y_.dot(W_) + b_)
            params_ += [W_, b_]
        
        return X_, Y_, params_ 

    """
    Avant d'appeler la fonction de train on doit charger les labels avec quelque chose comme:

        feature_dir = ...
        feature_extension = ...
    
        #Load the labels
        with open(label_file_name, 'r') as inputf:
            lines = [line.rstrip() for line in inputf]
            train_seg_list = [(feature_dir + line.split('_')[0] + feature_extension,
                 int(line.split('_')[1].split('-')[0]),
                 int(line.split(' ')[0].split('_')[1].split('-')[1]),
                 np.loadtxt(StringIO.StringIO(line), delimiter=' ', dtype='object')[1:].astype('int'))
                 for line in lines[1:-1]]        
    """
    
    def train(self,training_seg_list, 
                  cross_validation_seg_list,
                  feature_file_format,
                  feature_size,
                  feature_context=(7,7),
                  lr = 0.008,
                  segment_buffer_size=200,
                  batch_size=512,
                  max_iters=20,
                  tolerance=0.003,
                  log=None,
                  output_file_name="",
                  save_tmp_nnet=False):
        """
        :param train_seg_list: list of segments to use for training
            It is a list of 4 dimensional tuples which 
            first argument is the absolute file name
            second argument is the index of the first frame of the segment
            third argument is the index of the last frame of the segment
            and fourth argument is a numpy array of integer, 
            labels corresponding to each frame of the segment
        :param cross_validation_seg_list: is a list of segments to use for
            cross validation. Same format as train_seg_list
        :param feature_file_format: spro4 or htk
        :param feature_size: dimension of the acoustic feature
        :param feature_context: tuple of left and right context given in
            number of frames
        :param lr: initial learning rate
        :param segment_buffer_size: number of segments loaded at once
        :param batch_size: size of the minibatches as number of frames
        :param max_iters: macimum number of epochs
        :param tolerance:
        :param log: logger object used to output information
        """     
        np.random.seed(42)
        
        # shuffle the training list
        shuffle_idx = np.random.permutation(np.arange(len(training_seg_list)))
        training_seg_list = [training_seg_list[idx] for idx in shuffle_idx]        
        
        # If not done yet, compute mean and standard deviation on all training data
        if 0 in [len(self.params["input_mean"]), len(self.params["input_std"])]:
            import sys
            if sys.version_info[0] >= 3:
                print("Compute mean and standard deviation from the training features")
                feature_nb, self.params["input_mean"], self.params["input_std"] = mean_std_many(feature_file_format, 
                                                  feature_size,
                                                  training_seg_list, 
                                                  feature_context[0], 
                                                  feature_context[1])
                print("Au total on a {} trames".format(feature_nb))
            else:
                print("Print input mean and std from file ")
                ms = np.load("input_mean_std.npz")
                self.params["input_mean"]  = ms["input_mean"]
                self.params["input_std"] = ms["input_std"]



        # Instantiate the neural network, variables used to define the network
        # are defined and initialized
        X_, Y_, params_ = self.instantiate_network()
        
        # define a variable for the learning rate
        lr_ = T.scalar()
        
        # Define a variable for the output labels
        T_ = T.ivector("T")
        
        # Define the functions used to train the network
        cost_ = T.nnet.categorical_crossentropy(Y_, T_).sum()
        acc_ = T.eq(T.argmax(Y_, axis=1), T_).sum()
        params_to_update_ = [p for p in params_ if p.name[0] in "Wb"]
        grads_ = T.grad(cost_, params_to_update_)
    
        train = theano.function(
            inputs=[X_, T_, lr_],
            outputs=[cost_, acc_],
            updates=[(p, p - lr_ * g) for p, g in zip(params_to_update_, grads_)])
    
        xentropy = theano.function(inputs=[X_, T_], outputs=[cost_, acc_])
    
        ####################
        # CHARGE TOUT D'UN COUP
        #segment_buffer_size = len(training_seg_list)
        ####################


        # split the list of files to process
        training_segment_sets = [training_seg_list[i:i+segment_buffer_size] 
                        for i  in range(0, len(training_seg_list), segment_buffer_size)]

        print("On charge tout en {} fois.".format(len(training_segment_sets)))       

 
        # Initialized cross validation error
        last_cv_error=np.inf
        
        # Set the initial decay factor for the learning rate
        lr_decay_factor = 1
    
        # Iterate to train the network
        for kk in range(1,max_iters):
            lr *= lr_decay_factor  # update the learning rate

            error = accuracy = n = 0.0
            nfiles = 0
            
            # Iterate on the mini-batches
            for ii, training_segment_set in enumerate(training_segment_sets):
                l = []
                f = []
                for idx, val  in enumerate(training_segment_set):
                    filename, s, e, label = val
                    e = s + len(label)
                    l.append(label)
                    f.append(sidekit.frontend.features.get_context(
                        sidekit.frontend.io.read_feature_segment(filename, 
                                                               feature_file_format,
                                                               start=s-feature_context[0], 
                                                               stop=e+feature_context[1]), 
                        left_ctx=feature_context[0], 
                        right_ctx=feature_context[1], 
                        hamming=False))
                    
                lab = np.hstack(l).astype(np.int16)
                fea = np.vstack(f).astype(np.float32)
                assert np.all(lab != -1) and len(lab) == len(fea) # make sure that all frames have defined label
                print("Size of macrobatch is: {}, {}".format(fea.shape[0], fea.shape[1]))
                shuffle = np.random.permutation(len(lab))
                lab = lab.take(shuffle, axis=0)
                fea = fea.take(shuffle, axis=0)



                if(nfiles == 0):
                    startTime = time.time()
                    print("Top Chrono")
                if(nfiles == 5000):
                    full_time = startTime - time.time()
                    print("temps pour 5000: {}".format(full_time))


                nsplits = len(fea)/batch_size
                nfiles += len(training_segment_set)
                import sys
                print("taille des donnees completes: {}".format(sys.getsizeof(fea)))
                print("Start iterating on minibatches")


                for jj, (X, t) in enumerate(zip(np.array_split(fea, nsplits), np.array_split(lab, nsplits))):
                    err, acc = train(X.astype(np.float32), t.astype(np.int16), lr)
                    error += err; accuracy += acc; n += len(X)
                    #print("Iteration on minibatch {}".format(jj))
                #log.info("%d/%d | %f | %f ", nfiles, len(train_list), error / n, accuracy / n)
                print("{}/{} | {} | {} ".format(nfiles, len(training_seg_list), error / n, accuracy / n))
            
            error = accuracy = n = 0.0
            
            # Cross-validation
            for ii, cv_segment in enumerate(cross_validation_seg_list):
                filename, s, e, label = cv_segment
                e = s + len(label)
                t = label.astype(np.int16)
                X = sidekit.frontend.features.get_context(
                        sidekit.frontend.io.read_feature_segment(filename,
                                                           feature_file_format,
                                                           start=s-feature_context[0], 
                                                           stop=e+feature_context[1]), 
                        left_ctx=feature_context[0], 
                        right_ctx=feature_context[1], 
                        hamming=False)

                assert len(X) == len(t)
                err, acc = xentropy(X, t)
                error += err; accuracy += acc; n += len(X)
            
            
            
            # Save the current version of the network
            if save_tmp_nnet:
                np.savez(output_file_name + '_epoch'+str(kk), **get_params(params_))
            
            # Load previous weights if error increased
            if last_cv_error <= error:
                """A remplacer"""
                set_params(params_, last_params)
                error = last_cv_error
            
            # Start halving the learning rate or terminate the training
            if (last_cv_error-error)/np.abs([last_cv_error, error]).max() <= tolerance:
                if lr_decay_factor < 1: break
                lr_decay_factor = 0.5
            
            # Update the cross-validation error
            last_cv_error = error
            
            # get last computed params
            last_params = get_params(params_)
            set_params(self.params, params_)
        
        # Save final network
        model_name = output_file_name + '_'.join([str(ii) for ii in self.params["hidden_layer_sizes"]])
        np.savez(model_name, **get_params(params_))
    
    
    def save(self):
        pass
    
    def compute_stat(self):
        pass
    
    def estimate_gmm(self):
        pass






def mkdir_p(path):
    try:
        os.makedirs(path)
    except OSError as exc:
        if exc.errno == errno.EEXIST and os.path.isdir(path):
            pass
        else: raise

def create_theano_nn(param_dict):
    """
    param_dict contains X, Y, input_mean, input_std, 
    pairs of biais vector and weight matrix and the list of 
    activation functions to use in the different layers.
    
    len(param_dict.keys())/2 is the number of pairs: B, W + 2
    """
    X_ = T.matrix("X")
    mean_ = theano.shared(param_dict['input_mean'], name='input_mean')
    std_  = theano.shared(param_dict['input_std'], name='input_std')
    Y_ = (X_ - mean_) / std_
    params_ = [mean_, std_]
    activation_functions = param_dict["activation_functions"]
    n_hidden_layers = len(param_dict.keys())/2-2

    for ii, f in enumerate(param_dict["activation_functions"]):
        W_ = theano.shared(param_dict['W'+str(ii+1)], name='W'+str(ii+1))
        b_ = theano.shared(param_dict['b'+str(ii+1)], name='b'+str(ii+1))
        if f is None:
            Y_ = Y_.dot(W_) + b_
        else:
            Y_ = f(Y_.dot(W_) + b_)
        params_ += [W_, b_] 
    return X_, Y_, params_


def init_params(input_mean, input_std, hidden_layer_sizes, layers_activations, nclasses):
    """
    Activation function can be  T.nnet.sigmoid, T.nnet.relu, T.nnet.softmax, T.nnet.binary_crossentropy
    """

    assert len(layers_activations) == len(hidden_layer_sizes) + 1, "Mismatch between number of hidden layers and activation functions"
    
    sizes = (len(input_mean),)+tuple(hidden_layer_sizes)+(nclasses,)
    params_dict = {"input_mean": input_mean.astype(T.config.floatX), "input_std": input_std.astype(T.config.floatX)}
    for ii in range(1,len(sizes)):   params_dict['W'+str(ii)] = np.random.randn(sizes[ii-1],sizes[ii]).astype(T.config.floatX)*0.1
    for ii in range(1,len(sizes)-1): params_dict['b'+str(ii)] = np.random.random(           sizes[ii]).astype(T.config.floatX)/5.0-4.1
    params_dict['b'+str(len(sizes)-1)] = np.zeros(sizes[len(sizes)-1]).astype(T.config.floatX)
    params_dict['activation_functions'] = layers_activations
            
    return params_dict




def train_ff_dnn(stm_label_file,  # a remplacer par un IdMap pour la définition des segments et un repertoire de fichiers label à charger
                 train_list,
                 cv_list,
                 feature_dir,
                 feature_extension,
                 hidden_layer_sizes, 
                 layer_activations, 
                 output_file_name, 
                 lr=0.008, 
                 segment_buffer_size=200, 
                 batch_size=512, 
                 max_iters=20, 
                 tolerance=0.003,
                 save_tmp_nnet=False):
    """
    :param stm_label_file: label file generated by Kaldi, senone alignement per segment
    :param hidden_layer_sizes: tuple of integers, the number of cells per layer
    :param layer_activations: list of activation functions for each of the 
        layers
    :param output_file_name: name of the file where to store the network parameters
    
    """
    
    # Obtenir le nombre d'états utilisés par le système Kaldi-GMM
    """ A voir, est-ce que ce format est standard? on le garder ou on passe à du 100% SIDEKIT ?"""
    with open(stm_label_file, 'r') as inputf:
        lines = [line.rstrip() for line in inputf]
        total_seg_list = [(line.split('_')[0],
                 int(line.split('_')[1].split('-')[0]),
                 int(line.split(' ')[0].split('_')[1].split('-')[1]),
                 np.loadtxt(StringIO.StringIO(line), delimiter=' ', dtype='object')[1:].astype('int'))
                for line in lines[1:-1]]
        max_list = [seg[3].max() for seg in total_seg_list]
        nclasses = max(max_list)

    #seg_list = [total_seg_list[ii] for ii in idx[:len(total_seg_list)*0.9]]
    #cv_list = [total_seg_list[ii] for ii in idx[len(total_seg_list)*0.9:]]
    file_list = list(set([seg[0] for seg in train_list]))

    """ 
    Compute mean and standard deviation on a subset of the training features
    """
    log.info("Estimating mean and std at the NN input")
    N = F = S = 0.0
    for filename in file_list[::3]:
        print('Process file {}'.format(filename))
        features = []
        for seg in train_list:
            if seg[0] == filename and os.path.exists(feature_dir + seg[0] + '.fb'):
                features.append(sidekit.frontend.features.get_context(
                        sidekit.frontend.io.read_spro4_segment(feature_dir + seg[0] + feature_extension, 
                                                               start=seg[1]-left_context, 
                                                               end=seg[2]+right_context), 
                                                               left_ctx=left_context, 
                                                               right_ctx=right_context, 
                                                               hamming=False))
        feat = np.vstack(features)
        N += len(feat)
        F += np.sum(feat, axis=0)
        S += np.sum(feat**2, axis=0)

    input_mean = (F/N).astype("float32")
    input_std  = np.sqrt(S/N - input_mean**2).astype("float32")


    """
    Define the NN
    """
    # Neural network definition
    log.info("Creating and initializing NN")
    np.random.seed(42)

    X_, Y_, params_ = sidekit.theano_utils.create_theano_nn(sidekit.theano_utils.init_params(input_mean, input_std, hidden_layer_sizes, layer_activations, nclasses))

    lr_ = T.scalar()
    T_ = T.ivector("T")
    cost_ = T.nnet.categorical_crossentropy(Y_, T_).sum()
    acc_ = T.eq(T.argmax(Y_, axis=1), T_).sum()
    params_to_update_ = [p for p in params_ if p.name[0] in "Wb"]
    grads_ = T.grad(cost_, params_to_update_)

    train = theano.function(
        inputs=[X_, T_, lr_],
        outputs=[cost_, acc_],
        updates=[(p, p - lr_ * g) for p, g in zip(params_to_update_, grads_)])

    xentropy = theano.function(inputs=[X_, T_], outputs=[cost_, acc_])
    
    """
    Split the list of segments to process
    """
    segment_sets = [train_list[i:i+segment_buffer_size] for i  in range(0, len(train_list), segment_buffer_size)]
    last_cv_error=np.inf
    lr_decay_factor = 1


    """
    For each sub-list of segment
       - get the label and the indices of features to get
       - get contextualized features
       - Randomize the features
       - For each sub-set of segments, update the parameters of the NN

    """

    log.info("Training model: %s\nlearning rate: %f\nsegment buffer size: %d\nmini batch size: %d\nmax iters: %d tolerance: %f" % (
                 '->'.join(map(str, (len(input_mean),)+hidden_layer_sizes+(nclasses,))), lr, segment_buffer_size, batch_size, max_iters, tolerance))
    for kk in range(1,max_iters):
        lr *= lr_decay_factor
        log.info("Training epoch: %d, learning rate: %f", kk, lr)
        error = accuracy = n = 0.0; nfiles = 0
        for ii, segment_set in enumerate(segment_sets):

            l = []
            f = []
            for idx, val  in enumerate(segment_set):
                fn, s, e, label = val
                e = s + len(label)
                l.append(label)
                f.append(sidekit.frontend.features.get_context(sidekit.frontend.io.read_spro4_segment(feature_dir + fn + feature_extension, start=s-left_context, end=e+right_context), left_ctx=left_context, right_ctx=right_context, hamming=False))
                
            lab = np.hstack(l).astype(np.int16)
            fea = np.vstack(f).astype(np.float32)
            assert np.all(lab != -1) and len(lab) == len(fea) # make sure that all frames have defined label

            shuffle = np.random.permutation(len(lab))
            lab = lab.take(shuffle, axis=0)
            fea = fea.take(shuffle, axis=0) #faster than fea[shuffle]
            nsplits = len(fea)/batch_size
            nfiles += len(segment_set)
            for jj, (X, t) in enumerate(zip(np.array_split(fea, nsplits), np.array_split(lab, nsplits))):
                err, acc = train(X.astype(np.float32), t.astype(np.int16), lr)
                error += err; accuracy += acc; n += len(X)
            log.info("%d/%d | %f | %f ", nfiles, len(train_list), error / n, accuracy / n)
        log.info("Evaluating on CV")
        error = accuracy = n = 0.0
    
        for ii, segment in enumerate(cv_list):
            fn, s, e, label = segment
            e = s + len(label)
            t = label.astype(np.int16)
            X = sidekit.frontend.features.get_context(
                    sidekit.frontend.io.read_spro4_segment(feature_dir + fn + feature_extension, 
                                                           start=s-left_context, 
                                                           end=e+right_context), 
                    left_ctx=left_context, 
                    right_ctx=right_context, 
                    hamming=False)

            assert len(X) == len(t)
            err, acc = xentropy(X, t)
            error += err; accuracy += acc; n += len(X)
        log.info("%d | %f | %f", len(cv_list), error / n, accuracy / n)

        # Save the current version of the network
        if save_tmp_nnet:
            np.savez(output_file_name + '_epoch'+str(kk), **sidekit.theano_utils.get_params(params_))
        
        if last_cv_error <= error: # load previous weights if error increases
            sidekit.theano_utils.set_params(params_, last_params)
            error = last_cv_error
        if (last_cv_error-error)/np.abs([last_cv_error, error]).max() <= tolerance: # start halving learning rate or terminate the training 
            if lr_decay_factor < 1: break
            lr_decay_factor = 0.5
        last_cv_error = error
        last_params = sidekit.theano_utils.get_params(params_)

    np.savez(output_file_name, **sidekit.theano_utils.get_params(params_))




"""
Tout ce qui suit est à convertir mais on vera plus tard
"""
#def compute_stat_dnn(nn_file_name, idmap, fb_dir, fb_extension='.fb',
#                 left_context=15, right_context=15, dct_nb=16, feature_dir='', 
#                 feature_extension='', viterbi=False):
#    """
#    :param nn_file_name: weights and biaises of the network stored in npz format
#    :param idmap: class name, session name and start/ stop information 
#        of each segment to process in an IdMap object
#      
#    :return: a StatServer...
#    """
#    os.environ['THEANO_FLAGS']='mode=FAST_RUN,device=gpu,floatX=float32'
#    # Load weight parameters and create a network
#    X_, Y_, params_ = create_theano_nn(np.load(nn_file_name))
#    # Define the forward function to get the output of the network
#    forward =  theano.function(inputs=[X_], outputs=Y_)
#
#    # Create the StatServer
#    ss = sidekit.StatServer(idmap)
#    
#
#    # Compute the statistics and store them in the StatServer
#    for idx, seg in enumerate(idmap.rightids):
#        # Load the features
#        traps = sidekit.frontend.features.get_trap(
#                    sidekit.frontend.io.read_spro4_segment(fb_dir + seg + fb_extension, 
#                                                       start=idmap.start[idx]-left_context, 
#                                                       end=idmap.stop[idx]+right_context), 
#                    left_ctx=left_context, right_ctx=right_context, dct_nb=dct_nb)
#
#        feat = traps
#        if feature_dir != '' or feature_extension != '':
#            feat = sidekit.frontend.io.read_spro4_segment(feature_dir + seg + feature_extension, 
#                                                       start=idmap.start[idx], 
#                                                       end=idmap.stop[idx])
#            if feat.shape[0] != traps.shape[0]:
#                raise Exception("Parallel feature flows have different length")
#
#        # Process the current segment and get the stat0 per frame
#        s0 = forward(traps)
#        if viterbi:
#            max_idx = s0.argmax(axis=1)            
#            z = np.zeros((s0.shape)).flatten()
#            z[np.ravel_multi_index(np.vstack((np.arange(30),max_idx)), s0.shape)] = 1.
#            s0 = z.reshape(s0.shape)
#   
#        sv_size = s0.shape[1] * feat.shape[1]
#        
#        # Store the statistics in the StatServer
#        if ss.stat0.shape == (0,):
#            ss.stat0 = np.empty((idmap.leftids.shape[0], s0.shape[1]))
#            ss.stat1 = np.empty((idmap.leftids.shape[0], sv_size))
#            
#        ss.stat0[idx, :] = s0.sum(axis=0)
#        ss.stat1[idx, :] = np.reshape(np.dot(feat.T, s0).T, sv_size)
#    
#    return ss
#        
#
#def compute_ubm_dnn(nn_weights, idmap, fb_dir, fb_extension='.fb',
#                 left_context=15, right_context=15, dct_nb=16, feature_dir='',
#                 feature_extension='', label_dir = '', label_extension='.lbl',
#                 viterbi=False):
#    """
#    """
#    os.environ['THEANO_FLAGS']='mode=FAST_RUN,device=gpu,floatX=float32'
#    # Accumulate statistics using the DNN (equivalent to E step)
#    
#    # Load weight parameters and create a network
#    #X_, Y_, params_ = create_theano_nn(np.load(nn_file_name))
#    X_, Y_, params_ = nn_weights
#    ndim =  params_[-1].get_value().shape[0]  # number of distributions
#    
#    print("Train a UBM with {} Gaussian distributions".format(ndim))    
#    
#    # Define the forward function to get the output of the network
#    forward =  theano.function(inputs=[X_], outputs=Y_)
#
#    # Create the StatServer
#    ss = sidekit.StatServer(idmap)
#    
#
#    # Initialize the accumulator given the size of the first feature file
#    if feature_dir != '' or feature_extension != '':
#        feat_dim = sidekit.frontend.io.read_spro4_segment(feature_dir + idmap.rightids[0] + feature_extension, 
#                                                       start=0, 
#                                                       end=2).shape[1]
#    else:
#        feat_dim = sidekit.frontend.features.get_trap(
#                    sidekit.frontend.io.read_spro4_segment(fb_dir + idmap.rightids[0] + fb_extension, 
#                                                       start=0, 
#                                                       end=2), 
#                    left_ctx=left_context, right_ctx=right_context, dct_nb=dct_nb).shape[1]
#    
#    # Initialize one Mixture for UBM storage and one Mixture to accumulate the 
#    # statistics
#    ubm = sidekit.Mixture()
#    ubm.cov_var_ctl = np.ones((ndim, feat_dim))
#    
#    accum = sidekit.Mixture()
#    accum.mu = np.zeros((ndim, feat_dim))
#    accum.invcov = np.zeros((ndim, feat_dim))
#    accum.w = np.zeros(ndim)
#
#    # Compute the zero, first and second order statistics
#    for idx, seg in enumerate(idmap.rightids):
#        
#        start = idmap.start[idx]
#        end = idmap.stop[idx]
#        if start is None:
#            start = 0
#        if end is None:
#            endFeat = None
#            end = -2 * right_context
#        
#        
#        # Load speech labels
#        speech_lbl = sidekit.frontend.read_label(label_dir + seg + label_extension)
#        
#        # Load the features
#        traps = sidekit.frontend.features.get_trap(
#                    sidekit.frontend.io.read_spro4_segment(fb_dir + seg + fb_extension, 
#                                                       start=start-left_context, 
#                                                       end=end+right_context), 
#                    left_ctx=left_context, right_ctx=right_context, dct_nb=dct_nb)[speech_lbl, :]
#
#        feat = traps
#        if feature_dir != '' or feature_extension != '':
#            feat = sidekit.frontend.io.read_spro4_segment(feature_dir + seg + feature_extension, 
#                                                       start=max(start, 0), 
#                                                       end=endFeat)[speech_lbl, :]
#            if feat.shape[0] != traps.shape[0]:
#                raise Exception("Parallel feature flows have different length")
#
#        # Process the current segment and get the stat0 per frame
#        s0 = forward(traps)
#        if viterbi:
#            max_idx = s0.argmax(axis=1)            
#            z = np.zeros((s0.shape)).flatten()
#            z[np.ravel_multi_index(np.vstack((np.arange(30),max_idx)), s0.shape)] = 1.
#            s0 = z.reshape(s0.shape)
#   
#        sv_size = s0.shape[1] * feat.shape[1]
#        
#        # zero order statistics
#        accum.w += s0.sum(0)
#
#        #first order statistics
#        accum.mu += np.dot(feat.T, s0).T
#
#        # second order statistics
#        accum.invcov += np.dot(np.square(feat.T), s0).T     
#
#    # M step    
#    ubm._maximization(accum)
#    
#    return ubm
