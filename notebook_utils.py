"""
notebook_utils.py  -- import notebook_utils as nb
interactive utilities for profile development using ipython notebook
Created on Thu Aug 15 08:13:59 2013

@author: KEITHC
"""
import types
import inspect
import logging
import datetime 

import numpy  as np
import pandas as pd
import pylab
import networkx as nx

import analysis_engine
import analysis_engine.node as node
import hdfaccess.file

from analysis_engine import settings
import staged_helper  as helper  
from staged_helper import Flight, get_deps_series

        
def module_functions(mymodule):
    '''list non-private functions in a module'''
    return  [a for a in dir(mymodule) if isinstance(mymodule.__dict__.get(a), types.FunctionType) and a[0]!='_']
      
def timestamp():
    '''use to include a timestamp in a notebook'''
    n=datetime.datetime.now()
    return n.strftime('%Y/%m/%d %H:%M')
    
         
# add info: lfl flag, type, frequency, valid, count, mask count
def hdf_search(myhdf5, search_term):
    '''search over Series in an hdf5 file.  Partial matches ok; not case sensitive. 
	  e.g. param_search(ff, 'Accel')
    '''
    matching_names= [k for k in myhdf5.keys() if k.upper().find(search_term.upper())>=0]
    df = pd.DataFrame({'name': matching_names })
    df['recorded']= [ ('T' if myhdf5.get(nm).lfl else 'F') for nm in matching_names]
    df['frequency']= [ myhdf5.get(nm).frequency for nm in matching_names]
    df['data_type']= [ myhdf5.get(nm).data_type for nm in matching_names]    
    df['units']= [ (myhdf5.get(nm).units if myhdf5.get(nm).units else '') for nm in matching_names]
    
    values = []
    for nm in matching_names:
        if type(myhdf5.get(nm)) ==node.MultistateDerivedParameterNode:
            values.append(myhdf5.get(nm).values_mapping)
        else:
            values.append('n/a')
    df['values']= values
    return df


def _HDF2Series(par):
    '''convert a parameter array into a Pandas series indexed on flight seconds
		e.g. AG = par2series(ff['Gear On Ground']
    '''
    p2=np.where(par.array.mask,np.nan,par.array.data)
    return pd.Series( p2, index=ts_index(par))


def hdf_plot(hdf_series, label=None, kind='line', use_index=True, rot=None, xticks=None, yticks=None, xlim=None, ylim=None, ax=None, style=None, grid=None, legend=False, logx=False, logy=False):
    '''plot an hdf series with seconds on the x axis'''
    S = _HDF2Series(hdf_series)
    S.plot(label=label, kind=kind, use_index=use_index, rot=rot, xticks=xticks, yticks=yticks, xlim=xlim, ylim=ylim, ax=ax, style=style, grid=grid, legend=legend, logx=logx, logy=logy)


def node_type(base_nodes, nm):
    '''pretty version of node type for tabular display'''
    nodestr = repr(base_nodes[nm])
    if nodestr.find('key_point_values')>0: 
        ntype='KPV'
    elif nodestr.find('_phase')>0:
        ntype='phase'
    elif nodestr.find('_time_')>0:
        ntype='KTI'
    elif nodestr.find('_param')>0:
        ntype='parameter'
    else:
        ntype=nodestr        
    return ntype
    

def node_search(node_dict, search_term):
    '''search over a dict(name:node) of measurement nodes. partial matches ok; not case sensitive'
	  e.g. node_search(bn, 'flap')
    '''
    matching_names= [k for k in node_dict.keys() if k.upper().find(search_term.upper())>=0]
    df = pd.DataFrame({'name': matching_names })
    df['type']= [node_type(node_dict, nm) for nm in matching_names]
    #df.index = df['name']
    return df


def ts_index(par):
    '''given a parameter, construct a time array to serve as Series index
        e.g. ts_index(ff['Acceleration Normal'])
    '''
    return np.arange(par.offset, len(par.array)/par.frequency+par.offset,step=1/par.frequency)

	
def initialize_logger(LOG_LEVEL, filename='log_messages.txt'):
    '''all stages use this common logger setup'''
    logger = logging.getLogger()
    #logger = initialize_logger(LOG_LEVEL)
    logger.setLevel(LOG_LEVEL)
    logger.addHandler(logging.FileHandler(filename=filename)) #send to file 
    logger.addHandler(logging.StreamHandler())                #tee to screen
    return logger
    
    
def get_profile_nodes(myvars):
    ''' returns a dictionary of node classnames and class objects
        eg get_profile_nodes(vars())
    '''
    derived_nodes = {}
    nodelist =[(k,v) for (k,v) in myvars.items() if inspect.isclass(v) and issubclass(v,node.Node) and v.__module__ != 'analysis_engine.node']
    for k,v in nodelist:
        derived_nodes[k] = v
    return derived_nodes


def get_profile_nodemanager(flt, myvars):
    '''return a NodeManager for the current flight and profile definition
         normally myvars will be set myvars=vars() from a notebook    
    '''
    # full set of computable nodes
    requested_nodes = get_profile_nodes(myvars)  # get Nodes defined in the current namespace
    all_nodes = helper.get_derived_nodes(settings.NODE_MODULES)  #all the FDS derived nodes
    for k,v in requested_nodes.items():  # nodes in this profile
        all_nodes[k]=v
    for k,v in flt.series.items():  #hdf5 series
        all_nodes[k]=v
        
    node_mgr = node.NodeManager( flt.start_datetime, 
                        flt.duration, 
                        flt.series.keys(), #ff.valid_param_names(),  
                        requested_nodes.keys(), 
                        all_nodes, # computable
                        flt.aircraft_info,
                        achieved_flight_record={'Myfile':flt.filepath, 'Mydict':dict()}
                      )
    return node_mgr
    

def derive_many(flt, myvars, precomputed={}):
    '''simplified signature for deriving all nodes in a profile
        flt is an object of class Flight
        myvars normally=vars()
        precomputed is a dict of previously computed nodes
    '''
    node_mgr = get_profile_nodemanager(flt, myvars)
    process_order, graph = helper.dependency_order(node_mgr, draw=False)
    res, params = helper.derive_parameters_series(flt, node_mgr, process_order, precomputed={})
    return res, params
    

def derive_one(parameter_class, flight, precomputed={}):
    '''Pass in a single profile parameter node class to derive
       sample call:  node_graph(SimpleKPV)'''
    single_request = {parameter_class.__name__: parameter_class }
    
    # full set of computable nodes
    base_nodes = helper.get_derived_nodes(settings.NODE_MODULES)
    all_nodes = base_nodes.copy()
    for k,v in single_request.items():
        all_nodes[k]=v
    for k,v in flight.series.items():
        all_nodes[k]=v
    
    single_mgr = node.NodeManager( flight.start_datetime, 
                        flight.duration, 
                        flight.series.keys(),  
                        single_request.keys(), 
                        all_nodes, 
                        flight.aircraft_info,
                        achieved_flight_record={'Myfile': flight.filepath, 'Mydict':dict()}
                      )
    single_order, single_graph = helper.dependency_order(single_mgr, draw=False)
    res, params= helper.derive_parameters_series(flight, single_mgr, single_order, precomputed={})    
    return params


def derive_attr(attribute_class, flight, precomputed={}):
    '''if we are deriving an attribute, just return the value'''
    params = derive_one(attribute_class, flight, precomputed={})
    attr =  params.values()[0]        
    return attr.value
    

def _node_typestr(node):
    '''prettier version of node class type'''
    return str(node.node_type).replace("<class 'analysis_engine.node.",'').replace("'>",'')

def _param_val(param_node):
    '''prepare parameter values for nicer display '''
    if param_node.node_type is node.FlightAttributeNode:
        return param_node.value
    elif issubclass(param_node.node_type, node.SectionNode):
        if len(param_node.get_slices())==0:
            return '[]'    
        else:
            return ' '.join([ str(sl) for sl in param_node.get_slices() ]).replace('slice','')
    else: 
        return param_node
    
def derived_table(par):
    '''load derived parameters into a DataFrame for nice display'''
    outdf = pd.DataFrame({'name': [v for v in par.keys()]})
    outdf['node_type']= [_node_typestr(nd) for nd in par.values()] #outdf['node_type']
    outdf['val'] = [ _param_val(v) for v in par.values()]
    return outdf


def show_graph(graph, font_size=12):
    pylab.rcParams['figure.figsize'] = (16.0, 12.0)
    try:
        nx.draw_networkx(graph,pos=nx.spring_layout(graph), node_size=6, alpha=0.1, font_size=font_size)
    except:
        print 'hmm, apparently bogus exception'
    pylab.rcParams['figure.figsize'] = (10.0, 4.0)
    

def single_graph(parameter_class, flight):
    '''Pass in a profile parameter node class to view its dependency graph
       sample call:  node_graph(SimpleKPV)'''
    single_request = {parameter_class.__name__: parameter_class }
    
    # full set of computable nodes
    base_nodes = helper.get_derived_nodes(settings.NODE_MODULES)
    all_nodes = base_nodes.copy()
    for k,v in single_request.items():
        all_nodes[k]=v
    for k,v in flight.series.items():
        all_nodes[k]=v
        
    single_mgr = node.NodeManager( flight.start_datetime, 
                        flight.duration, 
                        flight.series.keys(),  
                        single_request.keys(), 
                        all_nodes, 
                        flight.aircraft_info,
                        achieved_flight_record={'Myfile':flight.filepath, 'Mydict':dict()}
                      )
    single_order, single_graph = helper.dependency_order(single_mgr, draw=False)
    show_graph(single_graph, font_size=12) 
  
  
if __name__=='__main__':
    initialize_logger('DEBUG')
    print module_functions(inspect)

    base_nodes = helper.get_derived_nodes(settings.NODE_MODULES)
    print node_search(base_nodes, 'flap')
    
    #hdf_plot(series['Vertical Speed'])
    print 'done'    