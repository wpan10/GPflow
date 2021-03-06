import tensorflow as tf
import numpy as np
from param import Param
from model import GPModel
import transforms
from .mean_functions import Zero
from tf_hacks import eye

class VGP(GPModel):
    def __init__(self, X, Y, kern, likelihood, mean_function=Zero(), num_latent=None):
        """
        X is a data matrix, size N x D
        Y is a data matrix, size N x R
        kern, likelihood, mean_function are appropriate GPflow objects

        This is the variational objective for the Variational Gaussian Process (VGP). The key reference is 

        @article{Opper:2009,
            title = {The Variational Gaussian Approximation Revisited},
            author = {Opper, Manfred and Archambeau, C{\'e}dric},
            journal = {Neural Comput.},
            year = {2009},
            pages = {786--792},
        }

        The idea is that the posterior over the function-value vector F is
        approximated by a Gaussian, and the KL divergence is minimised between the
        approximation and the posterior. It turns out that the optimal posterior
        precision shares off-diagonal elements with the prior, so only the diagonal
        elements of the prcision need be adjusted.

        The posterior approximation is

        q(f) = N(f | K alpha, [K^-1 + diag(square(lambda))]^-1)

        """
        GPModel.__init__(self, X, Y, kern, likelihood, mean_function)
        self.num_data = X.shape[0]
        self.num_latent = num_latent or Y.shape[1]
        self.q_alpha = Param(np.zeros((self.num_data, self.num_latent)))
        self.q_lambda = Param(np.ones((self.num_data, self.num_latent)), transforms.positive)

    def build_likelihood(self):
        """
        q_alpha, q_lambda are variational parameters, size N x R

        This method computes the variational lower lound on the likelihood, which is 

            E_{q(F)} [ \log p(Y|F) ] - KL[ q(F) || p(F)]
        with
            q(f) = N(f | K alpha, [K^-1 + diag(square(lambda))]^-1)
        """
        K = self.kern.K(self.X)
        f_mean = tf.matmul(K, self.q_alpha) + self.mean_function(self.X)
        #for each of the data-dimensions (columns of Y), find the diagonal of the
        #variance, and also relevant parts of the KL.
        f_var, A_logdet, trAi = [], tf.zeros((1,), tf.float64), tf.zeros((1,), tf.float64)
        for d in range(self.num_latent):
            b = self.q_lambda[:,d]
            B = tf.expand_dims(b, 1)
            A = eye(self.num_data) + K*B*tf.transpose(B)
            L = tf.cholesky(A)
            Li = tf.user_ops.triangular_solve(L, eye(self.num_data), 'lower')
            LiBi = Li / b
            #full_sigma:return tf.diag(b**-2) - LiBi.T.dot(LiBi)
            f_var.append(1./tf.square(b) - tf.reduce_sum(tf.square(LiBi),0))
            A_logdet += 2*tf.reduce_sum(tf.log(tf.user_ops.get_diag(L)))
            trAi += tf.reduce_sum(tf.square(Li))

        f_var = tf.transpose(tf.pack(f_var))

        KL = 0.5*(A_logdet + trAi - self.num_data*self.num_latent + tf.reduce_sum(f_mean*self.q_alpha))

        return tf.reduce_sum(self.likelihood.variational_expectations(f_mean, f_var, self.Y)) - KL
    
    def build_predict(self, Xnew):
        """
        The posterior varirance of F is given by

            q(f) = N(f | K alpha, [K^-1 + diag(lambda**2)]^-1)

        Here we projec this to F*, the values of the GP at Xnew which is given by

           q(F*) = N ( F* | K_{*F} alpha , K_{**} - K_{*f}[K_{ff} + diag(lambda**-2)]^-1 K_{f*} )

        """

        #compute kernelly things
        Kx = self.kern.K(Xnew, self.X)
        K = self.kern.K(self.X)
        Kd = self.kern.Kdiag(Xnew)

        #predictive mean
        f_mean = tf.matmul(Kx, self.q_alpha) + self.mean_function(Xnew)

        #predictive var
        f_var = []
        for d in range(self.num_latent):
            b = self.q_lambda[:,d]
            A = K + tf.diag(1./tf.square(b))
            L = tf.cholesky(A)
            LiKx = tf.user_ops.triangular_solve(L, tf.transpose(Kx), 'lower')
            f_var.append( Kd - tf.reduce_sum(tf.square(LiKx),0) )
        f_var = tf.pack(f_var)
        return f_mean, tf.transpose(f_var)

