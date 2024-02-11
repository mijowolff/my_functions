
from sklearn.model_selection import RepeatedStratifiedKFold
from scipy.spatial import distance
import numpy as np
import random
from numpy.linalg import inv
from progress.bar import ChargingBar

def circ_dist(x,y,all_pairs=False):
    
    # circular distance between angles in radians
    
    x=np.asarray(x)
    y=np.asarray(y)
    
    x=np.squeeze(x)
    y=np.squeeze(y)
    
    if all_pairs:
        x_new=np.tile(np.exp(1j*x),(len(y),1))
        y_new=np.transpose(np.tile(np.exp(1j*y),(len(x),1)))
        circ_dists= np.angle(x_new/y_new)
    else:
        circ_dists= np.angle(np.exp(1j*x)/np.exp(1j*y))
        
    return circ_dists

def covdiag(x):
    
    '''
    x (t*n): t iid observations on n random variables
    sigma (n*n): invertible covariance matrix estimator
    
    Shrinks towards diagonal matrix
    as described in Ledoit and Wolf, 2004
    '''
    
    t,n=np.shape(x)
    
    # de-mean
    x=x-np.mean(x,axis=0)
    
    #get sample covariance matrix
    sample=np.cov(x,rowvar=False,bias=True)
    
    #compute prior
    prior=np.zeros((n,n))
    np.fill_diagonal(prior,np.diag(sample))
    
    #compute shrinkage parameters
    d=1/n*np.linalg.norm(sample-prior,ord='fro')**2
    y=x**2
    r2=1/n/t**2*np.sum(np.dot(y.T,y))-1/n/t*np.sum(sample**2)
    
    #compute the estimator
    shrinkage=max(0,min(1,r2/d))
    sigma=shrinkage*prior+(1-shrinkage)*sample
    
    return sigma

def cosfun(theta,mu,basis_smooth,amplitude='default',offset='default'):

    if amplitude=='default':
        amplitude=.5
    if offset=='default':
        offset=.5
    return (offset+amplitude*np.cos(theta-mu))**basis_smooth

def basis_set_fun(theta_bins,u_theta,basis_smooth='default'):
        
    if basis_smooth=='default':
        basis_smooth=theta_bins.shape[0]-1
        
    smooth_bins=np.zeros(theta_bins.shape)
    
    for ci in range(theta_bins.shape[0]):
        temp_kernel=cosfun(u_theta,u_theta[ci],basis_smooth)
        temp_kernel=np.expand_dims(temp_kernel,axis=[1,2])
        temp_kernel=np.tile(temp_kernel,(1,theta_bins.shape[1],theta_bins.shape[2]))
        smooth_bins[ci,:,:]=np.sum(theta_bins*temp_kernel,axis=0)/sum(temp_kernel)                        
    
    return smooth_bins


#%%  distance-based orientation decoding using cross-validation
def dist_theta_kfold(data,theta,n_folds=8,n_reps=10,data_trn=None,basis_set=True,angspace='default',ang_steps=4,balanced_train_bins=True,balanced_cov=False,residual_cov=False,dist_metric='mahalanobis',verbose=True,new_version=True):
    
    if verbose:
        from progress.bar import ChargingBar

    if data_trn is None:
        data_trn=data
        
    if type(angspace)==str:
        if angspace=='default':
            angspace=np.arange(-np.pi,np.pi,np.pi/8) # default is 16 bins
        
    if np.array_equal(angspace,np.unique(theta)):
        ang_steps=1        
                
    bin_width=np.diff(angspace)[0]
    
    x_dummy=np.zeros(len(theta)) # needed for sklearn splitting function
    
    X_ts=data
    X_tr=data_trn    
    if len(X_tr.shape)<3:
        X_tr=np.expand_dims(X_tr,axis=-1)
        
    if len(X_ts.shape)<3:
        X_ts=np.expand_dims(X_ts,axis=-1)
            
    ntrls, nchans, ntps=np.shape(X_ts)  

    m_temp=np.zeros((len(angspace),nchans,ntps))
    m=m_temp
      
    if verbose:
        bar = ChargingBar('Processing', max=ntps*ang_steps*n_reps*n_folds)
    
    distances=np.empty((ang_steps,len(angspace),ntrls,ntps))
    
    distances[:]=np.NaN

    angspaces=np.zeros((ang_steps,len(angspace)))

    for ans in range(0,ang_steps): # loop over all desired orientation spaces
    
        angspace_temp=angspace+ans*bin_width/ang_steps
        angspaces[ans,:]=angspace_temp

    angspace_full=np.reshape(angspaces,(angspaces.shape[0]*angspaces.shape[1]),order='F')

    theta_dists=circ_dist(angspace_full,theta,all_pairs=True)
    theta_dists=theta_dists.transpose()  

    theta_dists_temp=np.expand_dims(theta_dists,axis=-1)
    theta_dists2=np.tile(theta_dists_temp,(1,1,ntps))

    for ans in range(0,ang_steps): # loop over all desired orientation spaces
    
        angspace_temp=angspace+ans*bin_width/ang_steps
        
        # convert orientations into bins
        temp=np.argmin(abs(circ_dist(angspace_temp,theta,all_pairs=True)),axis=1)
        ang_bin_temp=np.tile(angspace_temp,(len(theta),1))               
        bin_orient_rads=ang_bin_temp[:,temp][0,:]
        
        y_subst=temp
        y=bin_orient_rads
                
        rskf = RepeatedStratifiedKFold(n_splits=n_folds, n_repeats=n_reps) # get splitting object
        
        split_counter=0
        
        distances_temp=np.empty([len(angspace_temp),ntrls,n_reps,ntps])
        distances_temp[:]=np.NaN
               
        for train_index, test_index in rskf.split(X=x_dummy,y=y_subst): # loop over all train/test folds, and repepitions
            
            X_train, X_test = X_tr[train_index,:,:], X_ts[test_index,:,:]
            y_train, y_test = y[train_index], y[test_index]
            y_subst_train, y_subst_test = y_subst[train_index], y_subst[test_index]
                        
            irep=int(np.floor(split_counter/n_folds))
            split_counter=split_counter+1
          
            train_dat_cov = np.empty((0,X_train.shape[1],X_train.shape[2]))
            train_dat_cov[:]=np.NaN
            
            if balanced_train_bins: # average over same orientaions of training set, but make sure these averages are based on balanced trials
                count_min=min(np.bincount(y_subst_train))
                for c in range(len(angspace_temp)):
                    temp_dat=X_train[y_train==angspace_temp[c],:,:]
                    ind=random.sample(list(range(temp_dat.shape[0])),count_min)
                    m_temp[c,:,:]=np.mean(temp_dat[ind,:,:],axis=0)
                    if balanced_cov: # if desired, the data used for the covariance can also be balanced
                        if residual_cov: # take the residual, note that this should only be done if the cov data is balanced!
                            train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:]-np.mean(temp_dat[ind,:,:],axis=0), axis=0)
                        else:
                            train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:], axis=0)
            else:
                for c in range(len(angspace_temp)):
                    m_temp[c,:,:]=np.mean(X_train[y_train==angspace_temp[c],:,:],axis=0)
                    
            if basis_set: # smooth the averaged train data with basis set
                m=basis_set_fun(m_temp,angspace_temp,basis_smooth='default')
            else:
                m=m_temp
            
            if not balanced_cov:
                train_dat_cov=X_train # use all train trials if cov is not balanced                       
                    
            for tp in range(ntps):
                m_train_tp=m[:,:,tp]
                X_test_tp=X_test[:,:,tp]
                
                if dist_metric=='mahalanobis':
                    dat_cov_tp=train_dat_cov[:,:,tp]
                    if new_version: # with a lot of dimensions, first performing pca and then using euclidian distance is faster (when using cdist)
                        cov=covdiag(dat_cov_tp) # use covariance of the training data for pca
                        train_dat_cov_avg = dat_cov_tp.mean(axis=0)
                        X_test_tp_centered = X_test_tp - train_dat_cov_avg
                        m_train_tp_centered = m_train_tp -train_dat_cov_avg
                        evals,evecs = np.linalg.eigh(cov)
                        idx = evals.argsort()[::-1]
                        evals = evals[idx]
                        evecs = evecs[:,idx]
                        evals_sqrt = np.sqrt(evals)

                        # compute euclidan distance in whitented pca space (which is identical to mahalanobis distance)
                        distances_temp[:,test_index,irep,tp] = distance.cdist(np.dot(m_train_tp_centered,evecs)/evals_sqrt, np.dot(X_test_tp_centered,evecs)/evals_sqrt, 'euclidean')
                    else:
                        cov=inv(covdiag(dat_cov_tp)) 
                        distances_temp[:,test_index,irep,tp]=distance.cdist(m_train_tp,X_test_tp,'mahalanobis', VI=cov) # compute distances between all test trials, and average train trials
                else:                    
                    distances_temp[:,test_index,irep,tp]=distance.cdist(m_train_tp,X_test_tp,'euclidean')
                   
                if verbose:    
                    bar.next()

        distances[ans,:,:,:]=np.mean(distances_temp,axis=2,keepdims=False)
    
    distances=distances-np.mean(distances,axis=1,keepdims=True)
    distances_flat=np.reshape(distances,(distances.shape[0]*distances.shape[1],distances.shape[2],distances.shape[3]),order='F')
    distances_flat=distances_flat-np.mean(distances_flat,axis=0,keepdims=True)
    dec_cos=np.squeeze(-np.mean(np.cos(theta_dists2)*distances_flat,axis=0))

    # order the distances, such that same angle distances are in the middle
    # first, assign each theta to a bin from angspace_full
    temp=np.argmin(abs(circ_dist(angspace_full,theta,all_pairs=True)),axis=1)
    ang_bin_temp=np.tile(angspace_full,(len(theta),1))               
    theta_bins=ang_bin_temp[:,temp][0,:]

    # then, sort the distances based on the distances between the theta_bins
    theta_bin_dists=circ_dist(angspace_full,theta_bins,all_pairs=True)
    theta_bin_dists=theta_bin_dists.transpose()
    theta_bin_dists_abs=np.abs(theta_bin_dists)
    # get index of the minimum distance
    theta_bin_dists_min_ind=np.argmin(theta_bin_dists_abs,axis=0)

    distances_ordered=np.zeros((distances_flat.shape))

    shift_to=np.where(angspace_full==0)[0][0]
    for trl in range(len(theta)):
        distances_ordered[:,trl,:] = np.roll(distances_flat[:,trl,:], int(shift_to - theta_bin_dists_min_ind[trl]), axis=0)
    
    if verbose:
        bar.finish()
    
    return dec_cos,distances,distances_ordered,angspaces,angspace_full

#%%  orientation resconstrution using cross-validation, cross-temporal
def dist_theta_kfold_ct(data,theta,n_folds=8,n_reps=10,data_trn=None,basis_set=True,angspace='default',ang_steps=4,balanced_train_bins=True,balanced_cov=False,residual_cov=False,dist_metric='mahalanobis',verbose=True,new_version=True):
    
    if data_trn is None:
        data_trn=data
        
    if type(angspace)==str:
        if angspace=='default':
            angspace=np.arange(-np.pi,np.pi,np.pi/8) # default is 16 bins
        
    if np.array_equal(angspace,np.unique(theta)):
        ang_steps=1        
                
    bin_width=np.diff(angspace)[0]
    
    x_dummy=np.zeros(len(theta)) # needed for sklearn splitting function
    
    X_ts=data
    X_tr=data_trn    
    if len(X_tr.shape)<3:
        X_tr=np.expand_dims(X_tr,axis=-1)
        
    if len(X_ts.shape)<3:
        X_ts=np.expand_dims(X_ts,axis=-1)
            
    ntrls, nchans, ntps=np.shape(X_ts)  
    _,_,ntps_trn=np.shape(X_tr)

    m_temp=np.zeros((len(angspace),nchans,ntps))
    m=m_temp
    
    if dist_metric=='euclidean':
        cov_metric=False 
    
    if verbose:
        bar = ChargingBar('Processing', max=ang_steps*n_reps*n_folds*ntps)
    
    dec_cos=np.empty((ang_steps,ntrls,ntps,ntps))
    distances=np.empty((ang_steps,len(angspace),ntrls,ntps_trn,ntps))
    
    dec_cos[:]=np.NaN
    distances[:]=np.NaN

    angspaces=np.zeros((ang_steps,len(angspace)))

    for ans in range(0,ang_steps): # loop over all desired orientation spaces
    
        angspace_temp=angspace+ans*bin_width/ang_steps
        angspaces[ans,:]=angspace_temp

    angspace_full=np.reshape(angspaces,(angspaces.shape[0]*angspaces.shape[1]),order='F')

    theta_dists=circ_dist(angspace_full,theta,all_pairs=True)
    theta_dists=theta_dists.transpose()  

    theta_dists_temp=np.expand_dims(theta_dists,axis=-1)
    theta_dists2=np.tile(theta_dists_temp,(1,1,ntps_trn,ntps))

    for ans in range(0,ang_steps): # loop over all desired orientation spaces
    
        angspace_temp=angspace+ans*bin_width/ang_steps
        
        # convert orientations into bins
        temp=np.argmin(abs(circ_dist(angspace_temp,theta,all_pairs=True)),axis=1)
        ang_bin_temp=np.tile(angspace_temp,(len(theta),1))               
        bin_orient_rads=ang_bin_temp[:,temp][0,:]
        
        y_subst=temp
        y=bin_orient_rads
                
        rskf = RepeatedStratifiedKFold(n_splits=n_folds, n_repeats=n_reps) # get splitting object
        
        split_counter=0
        
        distances_temp=np.empty([len(angspace_temp),ntrls,n_reps,ntps_trn,ntps])
        distances_temp[:]=np.NaN
        
        theta_dists=circ_dist(angspace_temp,y,all_pairs=True)
        theta_dists=theta_dists.transpose()                
        
        theta_dists_temp=np.expand_dims(theta_dists,axis=-1)
        theta_dists_temp=np.expand_dims(theta_dists_temp,axis=-1)
        theta_dists_temp=np.expand_dims(theta_dists_temp,axis=-1)
        theta_dists2=np.tile(theta_dists_temp,(1,1,n_reps,ntps,ntps))
        theta_dists=np.tile(np.expand_dims(theta_dists,axis=-1),(1,1,ntps))
        
        angspace_dist=np.unique(np.round(theta_dists,10))
        if -angspace_dist[-1]==angspace_dist[0]:
            angspace_dist=np.delete(angspace_dist,len(angspace_dist)-1)
               
        for train_index, test_index in rskf.split(X=x_dummy,y=y_subst): # loop over all train/test folds, and repepitions
            
            X_train, X_test = X_tr[train_index,:,:], X_ts[test_index,:,:]
            y_train, y_test = y[train_index], y[test_index]
            y_subst_train, y_subst_test = y_subst[train_index], y_subst[test_index]
                        
            irep=int(np.floor(split_counter/n_folds))
            split_counter=split_counter+1
          
            train_dat_cov = np.empty((0,X_train.shape[1],X_train.shape[2]))
            train_dat_cov[:]=np.NaN
            
            if balanced_train_bins: # average over same orientaions of training set, but make sure these averages are based on balanced trials
                count_min=min(np.bincount(y_subst_train))
                for c in range(len(angspace_temp)):
                    temp_dat=X_train[y_train==angspace_temp[c],:,:]
                    ind=random.sample(list(range(temp_dat.shape[0])),count_min)
                    m_temp[c,:,:]=np.mean(temp_dat[ind,:,:],axis=0)
                    if balanced_cov: # if desired, the data used for the covariance can also be balanced
                        if residual_cov: # take the residual, note that this should only be done if the cov data is balanced!
                            train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:]-np.mean(temp_dat[ind,:,:],axis=0), axis=0)
                        else:
                            train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:], axis=0)
            else:
                for c in range(len(angspace_temp)):
                    m_temp[c,:,:]=np.mean(X_train[y_train==angspace_temp[c],:,:],axis=0)
                    
            if basis_set: # smooth the averaged train data with basis set
                m=basis_set_fun(m_temp,angspace_temp,basis_smooth='default')
            else:
                m=m_temp
            
            if not balanced_cov:
                train_dat_cov=X_train # use all train trials if cov is not balanced
                               
            # reshape test data for efficient distance computation
            X_test_rs=np.moveaxis(X_test,-1,1)
            X_test_rs=np.reshape(X_test_rs,(len(test_index)*ntps,X_test.shape[1]),order='C')

            for tp in range(ntps_trn):
                m_train_tp=m[:,:,tp]
                       
                if dist_metric=='mahalanobis':
                    dat_cov_tp=train_dat_cov[:,:,tp]
                    if new_version: # with a lot of dimensions, first performing pca and then using euclidian distance is faster (when using cdist)                            
                        cov=covdiag(dat_cov_tp) # use covariance of the training data for pca
                        train_dat_cov_avg = dat_cov_tp.mean(axis=0)
                        X_test_rs_centered = X_test_rs - train_dat_cov_avg
                        m_train_tp_centered = m_train_tp -train_dat_cov_avg
                        evals,evecs = np.linalg.eigh(cov)
                        idx = evals.argsort()[::-1]
                        evals = evals[idx]
                        evecs = evecs[:,idx]
                        evals_sqrt = np.sqrt(evals)

                        dists = distance.cdist(np.dot(m_train_tp_centered,evecs)/evals_sqrt, np.dot(X_test_rs_centered,evecs)/evals_sqrt, 'euclidean')
                    else:
                        cov=inv(covdiag(dat_cov_tp))  
                        dists=distance.cdist(m_train_tp,X_test_rs,'mahalanobis', VI=cov) # compute distances between all test trials, and average train trials
                else:                    
                    dists=distance.cdist(m_train_tp,X_test_rs,'euclidean')

                distances_temp[:,test_index,irep,tp,:]=dists.reshape(len(angspace_temp),len(test_index),ntps)
                
                if verbose:
                    bar.next()

        distances[ans,:,:,:,:]=np.mean(distances_temp,axis=2,keepdims=False)
    
    distances=distances-np.mean(distances,axis=1,keepdims=True)
    distances_flat=np.reshape(distances,(distances.shape[0]*distances.shape[1],distances.shape[2],distances.shape[3],distances.shape[4]),order='F')
    distances_flat=distances_flat-np.mean(distances_flat,axis=0,keepdims=True)
    dec_cos=np.squeeze(-np.mean(np.cos(theta_dists2)*distances_flat,axis=0))

    # order the distances, such that same angle distances are in the middle
    # first, assign each theta to a bin from angspace_full
    temp=np.argmin(abs(circ_dist(angspace_full,theta,all_pairs=True)),axis=1)
    ang_bin_temp=np.tile(angspace_full,(len(theta),1))               
    theta_bins=ang_bin_temp[:,temp][0,:]

    # then, sort the distances based on the distances between the theta_bins
    theta_bin_dists=circ_dist(angspace_full,theta_bins,all_pairs=True)
    theta_bin_dists=theta_bin_dists.transpose()
    theta_bin_dists_abs=np.abs(theta_bin_dists)
    # get index of the minimum distance
    theta_bin_dists_min_ind=np.argmin(theta_bin_dists_abs,axis=0)

    distances_ordered=np.zeros((distances_flat.shape))

    shift_to=np.where(angspace_full==0)[0][0]
    for trl in range(len(theta)):
        distances_ordered[:,trl,:,:] = np.roll(distances_flat[:,trl,:,:], int(shift_to - theta_bin_dists_min_ind[trl]), axis=0)
    
    if verbose:
        bar.finish()
    
    return dec_cos,distances,distances_ordered,angspaces,angspace_full      

#%%    
def dist_nominal_kfold(data,conditions,n_folds=8,n_reps=10,data_trn=None,balanced_train_bins=True,balanced_cov=False,residual_cov=False,dist_metric='mahalanobis',new_version=True):
    
    # time_now = time.time()
    
    if data_trn is None:
        data_trn=data
        
    x_dummy=np.zeros(len(conditions))
    u_conds=np.unique(conditions)
    
    # convert conditions to integers, in case they aren't
    y_subst=np.zeros(conditions.shape)       
    for c in range(len(u_conds)):
        y_subst[conditions==u_conds[c]]=c
    y_subst = y_subst.astype(int)
    u_conds=np.unique(y_subst)
    
    X_ts=data
    X_tr=data_trn    
    if len(X_tr.shape)<3:
        X_tr=np.expand_dims(X_tr,axis=-1)
        
    if len(X_ts.shape)<3:
        X_ts=np.expand_dims(X_ts,axis=-1)
                    
    ntrls, nchans, ntps=np.shape(X_ts)
    
    m=np.zeros((len(u_conds),nchans,ntps))   
    
    bar = ChargingBar('Processing', max=ntps*n_reps*n_folds)
    
    rskf = RepeatedStratifiedKFold(n_splits=n_folds, n_repeats=n_reps)
        
    distances_temp=np.empty([len(u_conds),ntrls,n_reps,ntps])
    distances_temp[:]=np.NaN  
    
    split_counter=0  
    
    y_subst=np.squeeze(y_subst)
    for train_index, test_index in rskf.split(X=x_dummy,y=y_subst):
                
        X_train, X_test = X_tr[train_index,:,:], X_ts[test_index,:,:]
        y_train, y_test = y_subst[train_index], y_subst[test_index]
                    
        irep=int(np.floor(split_counter/n_folds))
        split_counter=split_counter+1
      
        train_dat_cov = np.empty((0,X_train.shape[1],X_train.shape[2]))
        train_dat_cov[:]=np.NaN
        
        if balanced_train_bins:
            count_min=min(np.bincount(y_train))
            for c in range(len(u_conds)):
                temp_dat=X_train[y_train==u_conds[c],:,:]
                ind=random.sample(list(range(temp_dat.shape[0])),count_min)
                m[c,:,:]=np.mean(temp_dat[ind,:,:],axis=0)
                if balanced_cov:
                    if residual_cov:
                        train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:]-np.mean(temp_dat[ind,:,:],axis=0), axis=0)
                    else:
                        train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:], axis=0)
        else:
            for c in range(len(u_conds)):
                m[c,:,:]=np.mean(X_train[y_train==u_conds[c],:,:],axis=0)         
        
        if not balanced_cov:
            train_dat_cov=X_train                          
            
        for tp in range(ntps):
            m_train_tp=m[:,:,tp]
            X_test_tp=X_test[:,:,tp]
            
            if dist_metric=='mahalanobis':
                dat_cov_tp=train_dat_cov[:,:,tp]
                if new_version: # euclidian in pca space (same as mahalanobis distance) is faster for high-dimensional data
                    cov=covdiag(dat_cov_tp) # use covariance of the training data for pca
                    train_dat_cov_avg = dat_cov_tp.mean(axis=0)
                    X_test_tp_centered = X_test_tp - train_dat_cov_avg
                    m_train_tp_centered = m_train_tp -train_dat_cov_avg
                    evals,evecs = np.linalg.eigh(cov)
                    idx = evals.argsort()[::-1]
                    evals = evals[idx]
                    evecs = evecs[:,idx]
                    evals_sqrt = np.sqrt(evals)
                        # compute euclidan distance in whitented pca space (which is identical to mahalanobis distance)
                    distances_temp[:,test_index,irep,tp] = distance.cdist(np.dot(m_train_tp_centered,evecs)/evals_sqrt, np.dot(X_test_tp_centered,evecs)/evals_sqrt, 'euclidean')
                else:                                 
                    cov=inv(covdiag(dat_cov_tp))                                     
                    distances_temp[:,test_index,irep,tp]=distance.cdist(m_train_tp,X_test_tp,'mahalanobis', VI=cov)
            else:                    
                distances_temp[:,test_index,irep,tp]=distance.cdist(m_train_tp,X_test_tp,'euclidean')
                
            bar.next()

    distances=np.mean(distances_temp,axis=2,keepdims=False)
    
    pred_cond=np.argmin(distances,axis=0)
    temp=np.transpose(np.tile(y_subst,(pred_cond.shape[1],1)))
    dec_acc=pred_cond==temp
    
    distance_difference=np.zeros([ntrls,ntps])
    
    for cond in u_conds:
        temp1=distances[np.setdiff1d(u_conds,cond),:,:]
        temp2=temp1[:,y_subst==cond,:]
        distance_difference[y_subst==cond,:]=np.mean(temp2,axis=0,keepdims=False)-distances[cond,y_subst==cond,:]    
    
    bar.finish()
    return distance_difference,distances,dec_acc,pred_cond

#%%    
def dist_nominal_kfold_ct(data,conditions,n_folds=8,n_reps=10,data_trn=None,balanced_train_bins=True,balanced_cov=False,residual_cov=False,dist_metric='mahalanobis',new_version=True):
    
    if data_trn is None:
        data_trn=data
        
    x_dummy=np.zeros(len(conditions))
    u_conds=np.unique(conditions)
    
    # convert conditions to integers, in case they aren't
    y_subst=np.zeros(conditions.shape)       
    for c in range(len(u_conds)):
        y_subst[conditions==u_conds[c]]=c
    y_subst = y_subst.astype(int)
    u_conds=np.unique(y_subst)
    
    X_ts=data
    X_tr=data_trn    
    if len(X_tr.shape)<3:
        X_tr=np.expand_dims(X_tr,axis=-1)
        
    if len(X_ts.shape)<3:
        X_ts=np.expand_dims(X_ts,axis=-1)
          
    ntrls, nchans, ntps=np.shape(X_ts)
    
    m=np.zeros((len(u_conds),nchans,ntps))   
    
    bar = ChargingBar('Processing', max=ntps*n_reps*n_folds)
    
    rskf = RepeatedStratifiedKFold(n_splits=n_folds, n_repeats=n_reps)
        
    distances_temp=np.empty([len(u_conds),ntrls,n_reps,ntps,ntps])
    distances_temp[:]=np.NaN  
    
    split_counter=0  
       
    y_subst=np.squeeze(y_subst)
    for train_index, test_index in rskf.split(X=x_dummy,y=y_subst):
                
        X_train, X_test = X_tr[train_index,:,:], X_ts[test_index,:,:]
        y_train, y_test = y_subst[train_index], y_subst[test_index]
                    
        irep=int(np.floor(split_counter/n_folds))
        split_counter=split_counter+1
      
        train_dat_cov = np.empty((0,X_train.shape[1],X_train.shape[2]))
        train_dat_cov[:]=np.NaN
        
        if balanced_train_bins:
            count_min=min(np.bincount(y_train))
            for c in range(len(u_conds)):
                temp_dat=X_train[y_train==u_conds[c],:,:]
                ind=random.sample(list(range(temp_dat.shape[0])),count_min)
                m[c,:,:]=np.mean(temp_dat[ind,:,:],axis=0)
                if balanced_cov:
                    if residual_cov:
                        train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:]-np.mean(temp_dat[ind,:,:],axis=0), axis=0)
                    else:
                        train_dat_cov = np.append(train_dat_cov, temp_dat[ind,:,:], axis=0)
        else:
            for c in range(len(u_conds)):
                m[c,:,:]=np.mean(X_train[y_train==u_conds[c],:,:],axis=0)         
        
        if not balanced_cov:
            train_dat_cov=X_train                        
        
        # reshape test data for efficient distance computation
        X_test_rs=np.moveaxis(X_test,-1,1)
        X_test_rs=np.reshape(X_test_rs,(len(test_index)*ntps,X_test.shape[1]),order='C')

        for tp in range(ntps):
            m_train_tp=m[:,:,tp]
            
            if dist_metric=='mahalanobis':
                dat_cov_tp=train_dat_cov[:,:,tp]
                if new_version: # with a lot of dimensions, first performing pca and then using euclidian distance is faster (when using cdist)
                    cov=covdiag(dat_cov_tp) # use covariance of the training data for pca
                    train_dat_cov_avg = dat_cov_tp.mean(axis=0)
                    X_test_rs_centered = X_test_rs - train_dat_cov_avg
                    m_train_tp_centered = m_train_tp -train_dat_cov_avg
                    evals,evecs = np.linalg.eigh(cov)
                    idx = evals.argsort()[::-1]
                    evals = evals[idx]
                    evecs = evecs[:,idx]
                    evals_sqrt = np.sqrt(evals)
                        # compute euclidan distance in whitented pca space (which is identical to mahalanobis distance)
                    dists = distance.cdist(np.dot(m_train_tp_centered,evecs)/evals_sqrt, np.dot(X_test_rs_centered,evecs)/evals_sqrt, 'euclidean')
                else:
                    cov=inv(covdiag(dat_cov_tp))
                    dists=distance.cdist(m_train_tp,X_test_rs,'mahalanobis', VI=cov) # compute distances between all test trials, and average train trials
            else:                    
                dists=distance.cdist(m_train_tp,X_test_rs,'euclidean')
            
            distances_temp[:,test_index,irep,tp,:]=dists.reshape(len(u_conds),len(test_index),ntps)
            bar.next()

    distances=np.mean(distances_temp,axis=2,keepdims=False)
    
    pred_cond=np.argmin(distances,axis=0)
    temp=np.transpose(np.tile(y_subst,(pred_cond.shape[1],pred_cond.shape[2],1)))
    dec_acc=pred_cond==temp
    
    distance_difference=np.zeros([ntrls,ntps,ntps])
    
    for cond in u_conds:
        temp1=distances[np.setdiff1d(u_conds,cond),:,:,:]
        temp2=temp1[:,y_subst==cond,:,:]
        distance_difference[y_subst==cond,:,:]=np.mean(temp2,axis=0,keepdims=False)-distances[cond,y_subst==cond,:,:]    
    
    bar.finish()
    return distance_difference,distances,dec_acc,pred_cond
# #%%
# # orientation decoding, leave-out-out approach, as used in Wolff et al., 2017

# def mahalTune_func(data,theta,angspace='default',bin_width='default'):
    
    
#     if type(angspace)==str:
#         if angspace=='default':
#             angspace=np.arange(-np.pi,np.pi,np.pi/6)
            
#     if type(bin_width)==str:
#         if bin_width=='default':
#             bin_width=np.pi/6
    
#     if len(data.shape)<3:
#         data=np.expand_dims(data,axis=-1)
        
#     ntrls,nchans,ntps=data.shape
#     d_tune=np.empty([ntrls,len(angspace),ntps])
#     cos_amp=np.empty([ntrls,ntps])
#     trl_ind=np.arange(ntrls)
    
#     bar = ChargingBar('Processing', max=ntrls*ntps)
    
#     m=np.empty([len(angspace),nchans,ntps])
#     for trl in range(ntrls):
#         trn_dat=data[np.setdiff1d(trl_ind,trl)]
#         trn_angle=theta[np.setdiff1d(trl_ind,trl)]
#         for bi,ba in enumerate(angspace):
#             temp_dists=circ_dist(trn_angle,theta[trl])
            
#             m[bi,:,:]=np.mean(trn_dat[abs(circ_dist(angspace[bi],temp_dists))<bin_width,:,:],axis=0)
            
#         for ti in range(ntps):
            
#             cov=inverse_cov_fun(np.squeeze(trn_dat[:,:,ti]),'covdiag',inverse_method='inv')  
            
#             x_train_tp=m[:,:,ti]
#             x_test_tp=np.expand_dims(data[trl,:,ti],axis=0)
            
#             dist_temp=distance.cdist(x_train_tp,x_test_tp,metric='mahalanobis', VI=cov)
            
#             d_tune[trl,:,ti]=np.squeeze(dist_temp)
            
#             cos_amp[trl,ti]=-np.mean(np.cos(angspace)*np.transpose(np.squeeze(d_tune[trl,:,ti])))
            
#             bar.next()
    
#     bar.finish()
#     return cos_amp, d_tune
    


    
    

        
    
        
    
    
    
        