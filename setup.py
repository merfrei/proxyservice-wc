from setuptools import setup

setup(name='proxyservice-wc',
      version='0.1',
      description='Utilities to use Proxy Service with Scrapy',
      url='https://bitbucket.org/merfrei/proxyservice-wc',
      author='Emiliano M. Rudenick',
      author_email='emr.frei@gmail.com',
      license='MIT',
      packages=['proxyservice_wc'],
      install_requires=[
          'w3lib',
      ],
      zip_safe=False)
