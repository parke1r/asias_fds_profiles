# -*- coding: utf-8 -*-
"""
example_profile defines a set of measure nodes that run against FDS FlightDataAnalyzer base data.
All the major node types are covered.  The emphasis is on manually constructing extremely simple measures, to
better understand how the node types are structured.  

There are also a couple of examples where the node is derived from other measures, which is the more typical case.
"""
#@author: KEITHC, started April 2013

### Section 1: dependencies (see FlightDataAnalyzer source files for additional options)
import time
import os, glob, socket
import numpy as np
from analysis_engine.node import ( A,   FlightAttributeNode,               # one of these per flight. mostly arrival and departure stuff
                                   App, ApproachNode,                      # per approach
                                   P,   DerivedParameterNode,              # time series with continuous values 
                                   M,   MultistateDerivedParameterNode,    # time series with discrete values
                                   KTI, KeyTimeInstanceNode,               # a list of time points meeting some criteria
                                   KPV, KeyPointValueNode,                 # a list of measures meeting some criteria; multiples are allowed and common 
                                   S,   SectionNode,  FlightPhaseNode,      # Sections=Phases
                                   KeyPointValue, KeyTimeInstance, Section  # data records, a list of which goes into the corresponding node
                                 )
# A Node is a list of items.  Each item type is a class with the fields listed below:
#   FlightAttribute = name, value.  could be a scolor or a collection, e.g. list or dict
#   ApproachItem    = recordtype('ApproachItem',    'type slice airport runway gs_est loc_est ils_freq turnoff lowest_lat lowest_lon lowest_hdg', default=None)  
#   KeyTimeInstance = recordtype('KeyTimeInstance', 'index name datetime latitude longitude', default=None)
#   Section = namedtuple('Section',                  'name slice start_edge stop_edge')   #=Phase
#   KeyPointValue   = recordtype('KeyPointValue', '  index value name slice datetime latitude longitude', field_defaults={'slice':slice(None)}, default=None)
                            
from analysis_engine.library import (integrate, repair_mask, index_at_value, all_of, any_of)
# asias_fds stuff
import analyser_custom_settings as settings
import staged_helper  as helper 
import fds_oracle

   
### Section 2: measure definitions -- attributes, KTI, phase/section, KPV, DerivedParameter
#      DerivedParameters will cause a set of hdf5 files to be generated.
class SimpleAttribute(FlightAttributeNode):
    """
        A simple FlightAttribute constructed using the node.set_flight_attr() method. 
        start_datetime is used only to provide a dependency, which appears to be a requirement.
    """    
    def derive(self, start_datetime=A('Start Datetime')):
        self.set_flight_attr('Keith')


class FileAttribute(FlightAttributeNode):
    """
        Tests availability of 'Myfile' filename attribute, 
          assigned by staged_helper.py via the aircraft_info dictionary.
     """    
    def derive(self, filename=A('Myfile')):
        self.set_flight_attr(filename.value)


class MydictAttribute(FlightAttributeNode):
    '''a simple FlightAttribute to tests availability of Mydict, 
       a dictionary that was assigned by staged_helper.'''
    def derive(self, mydict=A('Mydict')):
        mydict.value['testkey'] = [1,2,3]
        self.set_flight_attr(mydict.value)

             
class SimpleKTI(KeyTimeInstanceNode):
    '''a simple KTI=Key Time Instance. start_datetime is used only to provide a dependency'''
    name='Simple KTI'
    def derive(self, start_datetime=A('Start Datetime')):
        #print 'in SimpleKTI'
        self.create_kti(3)      

class SimplerKTI(KeyTimeInstanceNode):
    '''manually built KTI, illustrating how KeyTimeInstanceNode act like a list of KTI objects.'''
    name='My Simpler KTI'
    def derive(self, start_datetime=A('Start Datetime')):
        #print 'in SimpleKTI'
        kti=KeyTimeInstance(index=700., name='My Simpler KTI') #freq=1hz and offset=0 by default
        self.append( kti )
    
class SimpleKPV(KeyPointValueNode):
    '''a simple KPV. start_datetime is used only to provide a dependency'''
    name='Simple KPV'
    units='fpm'
    def derive(self, start_datetime=A('Start Datetime')):
        self.create_kpv(3.0, 999.9)

class SimplerKPV(KeyPointValueNode):
    '''manually built KPV, illustrating how KeyPointValueNode act like a list of KPV objects.
       Also shows how a  KPV node can contain KPV objects with different names.'''
    units='deg'
    def derive(self, start_datetime=A('Start Datetime')):
        self.append(KeyPointValue(index=42.5, value=666.6,name='My Simpler KPV'))
        #print 'simpler KPV 2'
        self.append(KeyPointValue(index=42.5, value=666.6,name='My Simpler KPV 2'))

class TCASRAStart(KeyTimeInstanceNode):
    '''Time of up or down advisory, 
       a KTI based on TCAS Combined Control during the Airborne phase of flight.'''
    name = 'TCAS RA Start'

    def derive(self, tcas=M('TCAS Combined Control'), air=S('Airborne')):
        #print 'in TCASRAStart'
        # we don't want to distinguish between up and down corrective in this context
        dn_idx = np.ma.where(tcas.array == 'Down Advisory Corrective')
        tcas.array[dn_idx] = 'Up Advisory Corrective'
        
        self.create_ktis_on_state_change(
                    'Up Advisory Corrective',
                    tcas.array,
                    change='entering',
                    phase=air
                )                           
        return            


class InitialApproach(FlightPhaseNode):
    """ 
    A phase of derived from the 'Approach' and 'Final Appraoch' phases.  
    It covers the time interval from beginning of 'Approach' to beginning of 'Final Approach'.
    """
    #S()=section=phase.
    def derive(self, approach=S('Approach'), final=S('Final Approach') ):
        if len(approach)>0 and len(final)>0:
            dbegin = min([d.start_edge for d in approach])
            dend   = min([d.start_edge for d in final]) #exclude approach time
            self.create_phase(slice( dbegin, dend ))
        return
  

class DistanceTravelledInAir(DerivedParameterNode):
    """
     A simple derived parameter, i.e. a derived time series.  
     It may be used as an input to other calculations, but our current policy (Nov 2013) is not
     to store it to disk.
     
     Exercises the FDS integrate() function to derive distance from airspeed.  
     The calculation is limited to periods when the aircraft is not in the 'Grounded' phase of flight.
    """
    name='Distance Travelled In Air'
    units='nm'
    def derive(self, airspeed=P('Airspeed True'), grounded=S('Grounded') ):
        for section in grounded:                      # zero out travel on the ground
            airspeed.array[section.slice]=0.0         # this is already a copy 
        repaired_array = repair_mask(airspeed.array)  # to avoid integration hiccups 
        adist      = integrate( repaired_array, airspeed.frequency, scale=1.0/3600.0 )
        self.array = adist
        #helper.aplot({'air dist':adist, 'airspd':airspeed.array})


### Section 3: pre-defined test sets
def tiny_test():
    '''quick test set'''
    input_dir  = settings.BASE_DATA_PATH + 'tiny_test/'
    print 'tiny_test()', input_dir
    files_to_process = glob.glob(os.path.join(input_dir, '*.hdf5'))
    repo='linux'
    return repo, files_to_process

def ffd_test10():
    '''quick test set'''
    input_dir  = settings.BASE_DATA_PATH + 'ffd897/'
    print input_dir
    files_to_process = glob.glob(os.path.join(input_dir, '*.hdf5'))
    repo='linux'
    return repo, files_to_process


def test10_scratch():
    '''quick test set'''
    input_dir  = '/opt/scratch/test10/'
    print input_dir
    files_to_process = glob.glob(os.path.join(input_dir, '*.hdf5'))
    repo='cockpit'
    return repo, files_to_process

def test10():
    '''quick test set'''
    input_dir  = settings.BASE_DATA_PATH + 'test10/'
    print input_dir
    files_to_process = glob.glob(os.path.join(input_dir, '*.hdf5'))
    repo='linux'
    return repo, files_to_process
    
def test100():
    '''quick test set'''
    input_dir  = settings.BASE_DATA_PATH + settings.OPERATOR_FOLDER +'2012-07-13/'
    print input_dir
    files_to_process = glob.glob(os.path.join(input_dir, '*.hdf5'))[:100]
    repo='keith'
    return repo, files_to_process


def test_sql_jfk():
    '''sample test set based on query from Oracle fds_flight_record'''
    query = """select distinct file_path from fds_flight_record 
                 where 
                    file_repository='central' 
                    and orig_icao='KJFK' and dest_icao in ('KFLL','KMCO' )
                    --and rownum<15
                    """
    files_to_process = fds_oracle.flight_record_filepaths(query)[:40]
    repo='central'
    return repo, files_to_process

def test_sql_jfk_local():
    '''sample test set based on query from Oracle fds_flight_record'''
    repo='local'
    query = """select distinct file_path from fds_flight_record 
                 where 
                    file_repository='REPO' 
                    and orig_icao='KJFK' and dest_icao in ('KFLL','KMCO' )
                    --and rownum<15
                    """.replace('REPO',repo)
    files_to_process = fds_oracle.flight_record_filepaths(query)  #[:40]
    return repo, files_to_process


def fll_local():
    '''sample test set based on query from Oracle fds_flight_record'''
    repo='local'
    query = """select distinct file_path from fds_flight_record 
                 where 
                    file_repository='REPO' 
                    and dest_icao in ('KFLL')
                    """.replace('REPO',repo)
    files_to_process = fds_oracle.flight_record_filepaths(query) #[:40]
    return repo, files_to_process


def test_multifleet():
    """test set to verify treatment of multiple fleet types"""
    repo="linux"
    query="""select file_path from fds_flight_record 
           where fleet_series='B747-200' and file_repository='linux' and rownum<=5
        union all
        select file_path from fds_flight_record 
           where fleet_series='CRJ 700' and file_repository='linux' and rownum<=5
        union all
        select file_path from fds_flight_record 
           where fleet_series='A320-200' and file_repository='linux' and rownum<=5"""
    files_to_process = fds_oracle.flight_record_filepaths(query)
    return repo, files_to_process


def test_kpv_range():
    '''run against flights with select kpv values.'''
    repo = 'linux'
    query="""select distinct f.file_path --, kpv.name, kpv.value
                from fds_flight_record f join fds_kpv kpv 
                  on kpv.file_repository=f.file_repository and kpv.base_file_path=f.base_file_path
                 where f.file_repository='REPO' 
                   and f.base_file_path is not null
                   and f.orig_icao='KIAD' and f.dest_icao='KFLL'
                   and ( 
                         kpv.name='Airspeed 500 To 20 Ft Max' 
                      --and kpv.value between 100.0 and 200.0)
                      --  or (kpv.name='Simple Kpv' and kpv.value>100)
                       ) 
                order by file_path
                """.replace('REPO',repo)
    files_to_process = fds_oracle.flight_record_filepaths(query)[:40]
    return repo, files_to_process

    
if __name__=='__main__':
    ###CONFIGURATION ######################################################### 
    FILE_REPOSITORY, FILES_TO_PROCESS = test_multifleet() #test10() #test10() #test_kpv_range()    #test10_opt() ##test_sql_jfk_local() #tiny_test() #test_sql_jfk() #test10() #tiny_test() #test10_shared #test_kpv_range() 
    PROFILE_NAME = 'Example'  #+ '-'+ socket.gethostname()   
    COMMENT = 'adding username'
    LOG_LEVEL = 'WARNING'       
    MAKE_KML_FILES = False
    IS_PARALLEL = False
    ###############################################################
    module_names = [ os.path.basename(__file__).replace('.py','') ]#helper.get_short_profile_name(__file__)   # profile name = the name of this file
    print 'profile', PROFILE_NAME 
    print 'file count:', len(FILES_TO_PROCESS)
    print ' module names', module_names    
    t0 = time.time()
    
    if IS_PARALLEL:
        print "Run 'ipcluster start -n 10' from the command line first!"
        dview = helper.parallel_directview(PROFILE_NAME, module_names , FILE_REPOSITORY, 
                                                               LOG_LEVEL, FILES_TO_PROCESS, COMMENT, MAKE_KML_FILES)
        def eng_profile():
            import staged_helper
            reload(staged_helper)       
            status = staged_helper.run_profile(PROFILE_NAME , module_names, LOG_LEVEL, files_to_process, 
                                    COMMENT, MAKE_KML_FILES, file_repository, save_oracle=True, mortal=True )
            return status
        #engine_results = dview.apply(eng_profile) 
        client = dview.client
        print 'Engine Queue status', client.queue_status()
        engine_results = dview.apply(eng_profile) 
        status = engine_results[0]                    
        client.clear()
        client.close()

    else:
        status=helper.run_profile(PROFILE_NAME , module_names, LOG_LEVEL, FILES_TO_PROCESS, 
                                COMMENT, MAKE_KML_FILES, FILE_REPOSITORY, save_oracle=True, mortal=True )

    print 'time', time.time()-t0
    print 'status', status
    ts=status['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
    
    rpt_sql = helper.report_sql(PROFILE_NAME, status['timestamp'])
    for k in rpt_sql.keys():
        print "\n "+k +":"
        print rpt_sql[k]

    print 'done with PROFILE: '+'"'+PROFILE_NAME+'"'+'   TIMESTAMP: '+ts
