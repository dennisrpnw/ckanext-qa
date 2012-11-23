import os
import subprocess
import time
import requests
import json
import logging
import datetime
from functools import wraps
import copy

from nose.tools import raises, assert_equal
from ckan import model
from ckan.tests import BaseCase

import ckanext.qa.tasks
from ckanext.qa.tasks import resource_score, update, extension_variants
import ckanext.archiver
from ckanext.dgu.lib.formats import Formats

log = logging.getLogger(__name__)

# Monkey patch get_cached_resource_filepath so that it doesn't barf when
# it can't find the file
def mock_get_cached_resource_filepath(cache_url):
    if not cache_url:
        return None
    return cache_url.replace('http://remotesite.com/', '/resources')
ckanext.qa.tasks.get_cached_resource_filepath = mock_get_cached_resource_filepath

# Monkey patch sniff_file_format. This isolates the testing of tasks from
# actual sniffing
sniffed_format = None
def mock_sniff_file_format(filepath, log):
    return sniffed_format
ckanext.qa.tasks.sniff_file_format = mock_sniff_file_format
def set_sniffed_format(format_display_name):
    global sniffed_format
    if format_display_name:
        sniffed_format = Formats.by_display_name()[format_display_name]
    else:
        sniffed_format = None
    
class TestResourceScore(BaseCase):

    @classmethod
    def setup_class(cls):
        fake_ckan_path = os.path.join(os.path.dirname(__file__), "fake_ckan.py")
        cls.fake_ckan = subprocess.Popen(['python', fake_ckan_path])
        cls.fake_ckan_url = 'http://0.0.0.0:50001'

        #make sure services are running
        for i in range(0, 12):
            time.sleep(0.1)
            try:
                response = requests.get(cls.fake_ckan_url)
            except requests.ConnectionError:
                continue
            if response:
                break
        else:
            raise Exception('services did not start!')

        cls.fake_context = {
            'site_url': cls.fake_ckan_url,
            'apikey': u'fake_api_key',
            'site_user_apikey': u'fake_api_key',
        }
        cls.fake_resource = {
            'id': u'fake_resource_id',
            'url': 'http://remotesite.com/filename.csv',
            'cache_url': 'http://remotesite.com/resources/filename.csv',
            'cache_filepath': __file__, # must exist
            'package': u'fake_package_id',
            'is_open': True,
            'position': 2,
        }

    @classmethod
    def teardown_class(cls):
        cls.fake_ckan.kill()

    def test_by_sniff_csv(self):
        set_sniffed_format('CSV')
        data = self.fake_resource
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 3, result
        assert 'Content of file appeared to be format "CSV"' in result['openness_score_reason'], result

    def test_by_sniff_xls(self):
        set_sniffed_format('XLS')
        data = self.fake_resource
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 2, result
        assert 'Content of file appeared to be format "XLS"' in result['openness_score_reason'], result

    def test_not_cached(self):
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename'
        data['cache_url'] = None
        data['cache_filepath'] = None
        result = resource_score(self.fake_context, data, log)
        # falls back on fake_ckan task status data detailing failed attempts
        assert result['openness_score'] == 0, result
        assert 'File could not be downloaded' in result['openness_score_reason'], result
        assert 'Tried 16 times since 2008-10-01' in result['openness_score_reason'], result
        assert 'Error details: Server returned 500 error' in result['openness_score_reason'], result

    def test_by_extension(self):
        set_sniffed_format(None)
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename.xls'
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 2, result
        assert 'not recognised from its contents' in result['openness_score_reason'], result
        assert 'extension "xls" relates to format "XLS"' in result['openness_score_reason'], result

    def test_by_format_field(self):
        set_sniffed_format(None)
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename'
        data['format'] = 'XLS'
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 2, result
        assert 'not recognised from its contents' in result['openness_score_reason'], result
        assert 'Could not determine a file extension in the URL' in result['openness_score_reason'], result
        assert 'Format field "XLS"' in result['openness_score_reason'], result

    def test_by_format_field_excel(self):
        set_sniffed_format(None)
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename'
        data['format'] = 'Excel'
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 2, result
        assert 'not recognised from its contents' in result['openness_score_reason'], result
        assert 'Could not determine a file extension in the URL' in result['openness_score_reason'], result
        assert 'Format field "Excel"' in result['openness_score_reason'], result

    def test_extension_not_recognised(self):
        set_sniffed_format(None)
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename.zar' # unknown format
        data['format'] = ''
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 1, result
        assert 'not recognised from its contents' in result['openness_score_reason'], result
        assert 'URL extension "zar" is an unknown format' in result['openness_score_reason'], result

    def test_format_field_not_recognised(self):
        set_sniffed_format(None)
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename'
        data['format'] = 'ZAR'
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 1, result
        assert 'not recognised from its contents' in result['openness_score_reason'], result
        assert 'Could not determine a file extension in the URL' in result['openness_score_reason'], result
        assert 'Format field "ZAR" does not correspond to a known format' in result['openness_score_reason'], result

    def test_no_format_clues(self):
        set_sniffed_format(None)
        data = copy.deepcopy(self.fake_resource)
        data['url'] = 'http://remotesite.com/filename'
        data['format'] = ''
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 1, result
        assert 'not recognised from its contents' in result['openness_score_reason'], result
        assert 'Could not determine a file extension in the URL' in result['openness_score_reason'], result
        assert 'Format field is blank' in result['openness_score_reason'], result

    def test_available_but_not_open(self):
        set_sniffed_format('CSV')
        data = copy.deepcopy(self.fake_resource)
        data['is_open'] = False
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 0, result
        assert 'License not open' in result['openness_score_reason'], result

    def test_available_but_not_open_pdf(self):
        set_sniffed_format('PDF')
        data = copy.deepcopy(self.fake_resource)
        data['is_open'] = False
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 0, result
        assert 'License not open' in result['openness_score_reason'], result

    def test_not_available_and_not_open(self):
        set_sniffed_format('CSV')
        data = copy.deepcopy(self.fake_resource)
        data['is_open'] = False
        data['cache_url'] = None
        data['cache_filepath'] = None
        result = resource_score(self.fake_context, data, log)
        assert result['openness_score'] == 0, result
        # in preference it should report that it is not available
        assert 'File could not be downloaded. Reason: URL request failed. Tried 16 times since 2008-10-01. Error details: Server returned 500 error.' in result['openness_score_reason'], result


class TestExtensionVariants:
    def test_0_normal(self):
        assert_equal(extension_variants('http://dept.gov.uk/coins-data-1996.csv'),
                     ['csv'])

    def test_1_multiple(self):
        assert_equal(extension_variants('http://dept.gov.uk/coins.data.1996.csv.zip'),
                     ['csv.zip', 'zip'])
            
    def test_2_parameter(self):
        assert_equal(extension_variants('http://dept.gov.uk/coins-data-1996.csv?callback=1'),
                     ['csv'])

    def test_3_none(self):
        assert_equal(extension_variants('http://dept.gov.uk/coins-data-1996'),
                     [])
