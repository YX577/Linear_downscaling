""""
A module of functions for regression/downscaling
"""

#import necessary packages
def import_dependencies():
    import warnings
    warnings.filterwarnings('ignore')
    import xarray as xr
    import sklearn
    import pandas as pd
    import numpy as np
    from sklearn.linear_model import LogisticRegression, Lasso, LinearRegression
    from sklearn.model_selection import ShuffleSplit, cross_val_score, GridSearchCV
    from sklearn.metrics import log_loss
    import matplotlib.pyplot as plt
    import os
    import sys

import_dependencies()

def load_predictors():
    """
        Read in predictors from specified files.
        Input: None (change to filenames)
        Output: Merged predictors as an xarray object
    """
    ### TODO:
    #update this based on given predictors
    ROOT = '/glade/p/cisl/risc/rmccrary/DOE_ESD/LargeScale_DCA/ERA-I/mpigrid/' #where the files are saved
    EXT = '_19790101-20181231_dayavg_mpigrid.nc' #date range at the end of the file
    SERIES = 'ERAI_NAmerica'

    #The variables to use
    surface_predictors = ['mslp', 'uas', 'vas'] #, 'ps']
    #Each of these predictors is taken at each pressure level below
    other_predictors = ['Q', 'RH', 'U', 'V', 'Z', 'Vort', 'Div']
    levels = [500, 700, 850] #pressure levels

    #Surface predictors
    for var in surface_predictors:
        file = ROOT + var + '_' + SERIES + '_surf' + EXT
        if var == 'mslp': predictors = xr.open_dataset(file)[var]
        predictors = xr.merge([predictors, xr.open_dataset(file)[var]])

    #Other predictors (at multiple pressure levels)
    for var in other_predictors:
        for level in levels:
            file = ROOT + var + '_' + SERIES + '_p' + str(level) + EXT
            predictors = xr.merge([predictors, xr.open_dataset(file).rename({var: var + '_p' + str(level)})])

    return predictors

def zscore(variable):
    """
        Standardizing a variable (converting to z-scores)
        Input: an Xarray dataset
        Output: a standardized Xarray object
    """
    return (variable - np.mean(variable)) / np.std(variable)

def standardize(predictors):
    """
        Standardizes predictors (assumes normality)
        Input: predictors xarray object
        Output: predictors xarray object
    """
    for col in [i for i in predictors.keys()]:
        #standardize each predictor
        predictors[col] = zscore(predictors[col])
        return predictors

def prep_data(obsPath, predictors, dateStart = '1980-01-01', dateEnd = '2014-12-31'):
    """
        Creating readable xarray objects from obs data and predictors
        Input:
            obsPath: filepath entered as a commandline argument
            predictors: existing xarray obj
            dateStart (optional): cutoff date for start of data
            dateEnd (optional): cutoff date for end of data
        Output:
            X_all, an xarray object of predictors
            Y_all, an xarray object of obs
    """
    obs = xr.open_mfdataset(obsPath).sel(time = slice(dateStart, dateEnd))
    X_all = predictors.sel(lat = lat, lon = lon, method = 'nearest').sel(time = slice(dateStart, dateEnd))
    Y_all = obs.sel(lat = lat, lon = lon, method = 'nearest').sel(time = slice(dateStart, dateEnd))

def add_month(X_all, Y_all):
    """
        Add month as a predictor in order to make separate models
        Input:
            X_all, an xarray object of predictors
            Y_all, an xarray object of obs
        Output:
            X_all, an xarray object of predictors with a month col
            Y_all, an xarray object of obs with a month col
            all_preds, a list of all keys in the predictors object
    """

    for dataset in [X_all, Y_all]:
        #adding a variable for month into the input and obs datasets
        all_preds = [key for key in dataset.keys()] #the names of the predictors
        dataset['month'] = dataset.time.dt.month
        if not 'month' in all_preds: all_preds += ['month'] #adding "month" to the list of variable names
    return X_all, Y_all, all_preds

def add_constant_col(X_all, all_preds):
    """
        Adds a column of ones for a constant (since the obs aren't normalized they aren't centered around zero)
        Input:
            X_all, xarray obj of predictors
            all_preds, list of predictors
        Output:
            X_all, xarray obj of predictors
            all_preds, list of predictors
    """
    X_all['constant'] = 1 + 0*X_all[all_preds[0]] #added the last part so it's dependent on lat, lon, and time
    if not 'constant' in all_preds:
        all_preds += ['constant'] #adding "constant" to the list of variable names
    return X_all, all_preds

def evenOdd(ds):
    """
        Separate even years for training and odd for testing
        Input: xarray dataset
        Output: even and odd year datasets as xarray objects
    """
    ds['time-copy'] = ds['time'] #backup time
    #classify years as even or odd
    ds['time'] =  pd.DatetimeIndex(ds.time.values).year%2 == 0
    even, odd = ds.sel(time = True), ds.sel(time = False)
    #reset to original time data
    even['time'], odd['time'] = even['time-copy'],odd['time-copy']
    return even.drop('time-copy'), odd.drop('time-copy')

def add_month_filter(data):
    """
        Set time dim to just the month. Save time as a variable.
        Input: xarray obj
        Output: xarray obj
    """
    data['timecopy'] = data['time']
    data['time'] = data['month']
    return data

def fit_linear_model(X, y, keys=None):
    """
        Use linear algebra to compute the betas for a multiple linear regression.
        Input: X (predictors) and y (obs) as xarray objects
        Output: a pandas dataframe with the betas (estimated coefficients) for each predictor.
    """
    if not type(X) is np.matrixlib.defmatrix.matrix:
        keys = [key for key in X.keys()]
        X = np.matrix([X[key].values for key in keys]).transpose() #X matrix; rows are days, columns are variables
    XT = X.transpose()
    betas = np.matmul(np.matmul(np.linalg.inv(np.matmul(XT,X)), XT), y)
    b = pd.DataFrame(index = range(1))
    for i in range(len(keys)):
        b[keys[i]] = betas[0,i] #assigning names to each coefficient
    return b

def fit_monthly_linear_models(X_train, Y_train, preds_to_keep):
    """
        Fits a linear model for each month of training data
        Input:
            X_train, an xarray obj of predictors
            Y_train, an xarray obj of observed predictands
            preds_to_keep, a list of key values of predictors to include in the regression
        Output:
            coefMatrix, an array of betas from the regression for each month
    """
    for month in range(1, 13):
        #get obs data
        y = Y_train.sel(time = month).tmax.values #obs values

        #get just subset of predictors
        x_train_subset = np.matrix([X_train.sel(time = month)[key].values for key in preds_to_keep]).transpose()
        x_test_subset = np.matrix([X_test.sel(time = month)[key].values for key in preds_to_keep]).transpose()

        #calculate coefficients for training data
        coefs = fit_linear_model(x_train_subset, y,
                    keys=preds_to_keep).rename(index = {0: 'coefficient'}).transpose()

        if month == 1:
            coefMatrix = coefs

        coefMatrix[monthsFull[month-1]] = coefs.values

    coefMatrix = coefMatrix.drop('coefficient', axis=1)
    return coefMatrix

def save_betas(save_path, coefMatrix):
    """
        Save betas to disk
        Input:
            save_path, a string containing the desired save location
        Output:
            None
    """
    ROOT = os.path.join(save_path,'betas')
    try:
        os.mkdir(ROOT)
    except FileExistsError:
        pass
    betas = "coefMatrix"
    fp = os.path.join(ROOT, '{}_tmax_{}_{}.nc'.format(betas, str(lat),str(lon)))
    try:
        os.remove(fp)
    except: pass
    eval(betas).to_csv(fp)

def predict_linear(X_all, betas):
    """
        Predict using each monthly model then combine all predicted data.
        Input:
            X_all, an xarray obj with predictors
            betas, an array of coefficients for each month
        Output:
            an xarray obj containing a 'preds' column
    """
    X_all_cp = X_all
    X_all_cp['time'] = X_all_cp['time-copy'].dt.month
    X_all_hand = [np.matrix([X_all_cp.sel(time = month)[key].values for key in preds_to_keep]).transpose() for month in range(1,13)]
    for month in range(1,13):
        X_month = X_all.sel(time=month)
        X_month["preds"] = X_month['time'] + X_month['lat']
        X_month["preds"]= ({'time' : 'time'}, np.matmul(x_all[month-1], betas[monthsFull[month-1]]))
        X_month['time'] = X_month['time-copy']
        if month == 1:
            X_preds = 0
            X_preds = X_month
        else:
            X_preds = xr.concat([X_preds, X_month], dim = "time")

    return X_preds.sortby('time')

def save_preds(save_path, preds):
    """
        Saves predictions to disk as netcdf
        Input:
            save_path as str
            preds, xarray obj
        Output:
            None
    """
    ROOT = os.path.join(save_path,'preds')
    try:
        os.mkdir(ROOT)
    except FileExistsError:
        pass
    fp = os.path.join(ROOT, 'finalPreds_{}_tmax_{}_{}.nc'.format(model, str(lat),str(lon)))
    try:
        os.remove(fp)
    except: pass
    preds.to_netcdf(fp)

















    #
