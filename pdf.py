# -*- coding: utf-8 -*-
#
# Copyright (c) 2010-2011, Monash e-Research Centre
#   (Monash University, Australia)
# Copyright (c) 2010-2011, VeRSI Consortium
#   (Victorian eResearch Strategic Initiative, Australia)
# All rights reserved.
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
#    *  Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    *  Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#    *  Neither the name of the VeRSI, the VeRSI Consortium members, nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE REGENTS AND CONTRIBUTORS ``AS IS'' AND ANY
# EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE REGENTS AND CONTRIBUTORS BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

"""
pdf.py

.. moduleauthor:: James Wettenhall <james.wettenhall@monash.edu>

"""
from fractions import Fraction
import logging

from django.conf import settings

from tardis.tardis_portal.models import Schema, DatafileParameterSet
from tardis.tardis_portal.models import ParameterName, DatafileParameter
from tardis.tardis_portal.models import DataFileObject
import subprocess
import tempfile
import os
import shutil
import traceback
import urlparse

logger = logging.getLogger(__name__)


class PdfImageFilter(object):
    """This filter uses ImageMagick's convert to display a preview
    image of a PDF file.

    :param name: the short name of the schema.
    :type name: string
    :param schema: the name of the schema to load the previewImage into.
    :type schema: string
    :param tagsToFind: a list of the tags to include.
    :type tagsToFind: list of strings
    :param tagsToExclude: a list of the tags to exclude.
    :type tagsToExclude: list of strings
    """
    def __init__(self, name, schema,
                 tagsToFind=[], tagsToExclude=[]):
        self.name = name
        self.schema = schema
        self.tagsToFind = tagsToFind
        self.tagsToExclude = tagsToExclude

    def __call__(self, sender, **kwargs):
        """post save callback entry point.

        :param sender: The model class.
        :param instance: The actual instance being saved.
        :param created: A boolean; True if a new record was created.
        :type created: bool
        """
        instance = kwargs.get('instance')

        if not instance.filename.lower().endswith('.pdf'):
            return None

        print "Applying PDF filter to '%s'..." % instance.filename

        schema = self.getSchema()

        tmpdir = tempfile.mkdtemp()

        filepath = os.path.join(tmpdir, instance.filename)
        logger.info("filepath = '" + filepath + "'")

        with instance.file_object as f:
            with open(filepath, 'wb') as g:
                while True:
                    chunk = f.read(1024)
                    if not chunk:
                        break
                    g.write(chunk)

        try:
            outputextension = "png"
            dfos = DataFileObject.objects.filter(datafile=instance)
            preview_image_rel_file_path = os.path.join(
                os.path.dirname(urlparse.urlparse(dfos[0].uri).path),
                str(instance.id),
                '%s.%s' % (os.path.basename(filepath),
                           outputextension))
            logger.info("preview_image_rel_file_path = " +
                        preview_image_rel_file_path)
            preview_image_file_path = os.path.join(
                settings.METADATA_STORE_PATH, preview_image_rel_file_path)
            logger.info("preview_image_file_path = " + preview_image_file_path)

            if not os.path.exists(os.path.dirname(preview_image_file_path)):
                os.makedirs(os.path.dirname(preview_image_file_path))

            self.fileoutput('/usr/bin',
                            'convert',
                            filepath + '[0]',
                            preview_image_file_path)

            metadata_dump = dict()
            metadata_dump['previewImage'] = preview_image_rel_file_path

            shutil.rmtree(tmpdir)

            self.saveMetadata(instance, schema, metadata_dump)

        except Exception, e:
            print str(e)
            print traceback.format_exc()
            logger.debug(str(e))
            return None

    def saveMetadata(self, instance, schema, metadata):
        """Save all the metadata to a Dataset_Files paramamter set.
        """
        parameters = self.getParameters(schema, metadata)

        # Some/all? of these excludes below are specific to DM3 format:

        exclude_line = dict()

        if not parameters:
            print "Bailing out of saveMetadata because of 'not parameters'."
            return None

        try:
            ps = DatafileParameterSet.objects.get(schema=schema,
                                                  datafile=instance)
            print "Parameter set already exists for %s, so we'll just " \
                "return it." % instance.filename
            return ps  # if already exists then just return it
        except DatafileParameterSet.DoesNotExist:
            ps = DatafileParameterSet(schema=schema,
                                      datafile=instance)
            ps.save()

        for p in parameters:
            print p.name
            if p.name in metadata:
                dfp = DatafileParameter(parameterset=ps,
                                        name=p)
                if p.isNumeric():
                    if metadata[p.name] != '':
                        dfp.numerical_value = metadata[p.name]
                        dfp.save()
                else:
                    print p.name
                    if isinstance(metadata[p.name], list):
                        for val in reversed(metadata[p.name]):
                            strip_val = val.strip()
                            if strip_val:
                                if strip_val not in exclude_line:
                                    dfp = DatafileParameter(parameterset=ps,
                                                            name=p)
                                    dfp.string_value = strip_val
                                    dfp.save()
                    else:
                        dfp.string_value = metadata[p.name]
                        dfp.save()

        return ps

    def getParameters(self, schema, metadata):
        """Return a list of the paramaters that will be saved.
        """
        param_objects = ParameterName.objects.filter(schema=schema)
        parameters = []
        for p in metadata:

            if self.tagsToFind and p not in self.tagsToFind:
                continue

            if p in self.tagsToExclude:
                continue

            parameter = filter(lambda x: x.name == p, param_objects)

            if parameter:
                parameters.append(parameter[0])
                continue

            # detect type of parameter
            datatype = ParameterName.STRING

            # Int test
            try:
                int(metadata[p])
            except ValueError:
                pass
            except TypeError:
                pass
            else:
                datatype = ParameterName.NUMERIC

            # Fraction test
            if isinstance(metadata[p], Fraction):
                datatype = ParameterName.NUMERIC

            # Float test
            try:
                float(metadata[p])
            except ValueError:
                pass
            except TypeError:
                pass
            else:
                datatype = ParameterName.NUMERIC

        return parameters

    def getSchema(self):
        """Return the schema object that the paramaterset will use.
        """
        try:
            return Schema.objects.get(namespace__exact=self.schema)
        except Schema.DoesNotExist:
            schema = Schema(namespace=self.schema, name=self.name,
                            type=Schema.DATAFILE)
            schema.save()
            return schema

    def exec_command(self, cmd):
        """execute command on shell
        """
        p = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            shell=True)

        p.wait()

        result_str = p.stdout.read()

        return result_str

    def fileoutput(self,
                   cd, execfilename, inputfilename, outputfilename, args=""):
        """execute command on shell with a file output
        """
        cmd = "cd '%s'; ./'%s' '%s' '%s' %s" %\
            (cd, execfilename, inputfilename, outputfilename, args)
        print cmd
        logger.info(cmd)

        return self.exec_command(cmd)

    def fileoutput2(self, cd, execfilename, inputfilename, args1,
                    outputfilename, args2=""):
        """execute command on shell with a file output
        """
        cmd = "cd '%s'; ./'%s' '%s' %s '%s' %s" %\
            (cd, execfilename, inputfilename, args1, outputfilename, args2)
        print cmd
        logger.info(cmd)

        return self.exec_command(cmd)

    def textoutput(self, cd, execfilename, inputfilename, args=""):
        """execute command on shell with a stdout output
        """
        cmd = "cd '%s'; ./'%s' '%s' %s" %\
            (cd, execfilename, inputfilename, args)
        print cmd
        logger.info(cmd)

        return self.exec_command(cmd)


def make_filter(name='', schema='', tagsToFind=[], tagsToExclude=[]):
    if not name:
        raise ValueError("PdfImageFilter "
                         "requires a name to be specified")
    if not schema:
        raise ValueError("PdfImageFilter "
                         "requires a schema to be specified")
    return PdfImageFilter(name, schema, tagsToFind, tagsToExclude)

make_filter.__doc__ = PdfImageFilter.__doc__
