# Unless explicitly stated otherwise all files in this repository are licensed
# under the Apache License Version 2.0.
# This product includes software developed at Datadog (https://www.datadoghq.com/).
# Copyright 2017 Datadog, Inc.

from __future__ import print_function

import base64
import json
import os
import re
import socket
import ssl
import urllib
import zlib

import boto3

# Parameters
# DD_API_KEY: Datadog API Key
DD_API_KEY = os.environ.get('DD_API_KEY')
HOST = 'intake.logs.datadoghq.com'
SSL_PORT = 10516
CT_REGEX = re.compile('\d+_CloudTrail_\w{2}-\w{4,9}-\d_\d{8}T\d{4}Z.+.json.gz$', re.I)
DD_SOURCE = 'ddsource'
DD_CUSTOM_TAGS = 'ddtags'
METADATA = {
    'ddsourcecategory': 'aws',
}
CUSTOM_TAGS = {}


try:
    METADATA = merge_dicts(METADATA, json.loads(os.environ.get('METADATA', '{}')))
except Exception:
    pass

try:
    CUSTOM_TAGS = merge_dicts(CUSTOM_TAGS, json.loads(os.environ.get('CUSTOM_TAGS', '{}')))
except Exception:
    pass



def lambda_handler(event, context):
    # Check prerequisites
    if DD_API_KEY is None:
        raise Exception(
            'You must configure your API key before starting this lambda '
            'function (see #Parameters section)'
        )

    # Attach Datadog's Socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    port = SSL_PORT
    sock = ssl.wrap_socket(s)
    sock.connect((HOST, port))

    # Add the context to meta
    if 'aws' not in METADATA:
        METADATA['aws'] = {}
    aws_meta = METADATA['aws']
    aws_meta['function_version'] = context.function_version
    aws_meta['invoked_function_arn'] = context.invoked_function_arn
    #Add custom tags here by adding new value with the following format 'key1:value1, key2:value2'  - might be subject to modifications
    custom_tags = 'functionname:{},memorysize:{}'.format(context.function_name,
                                                                      context.memory_limit_in_mb)
    #Add remaining CUSTOM_TAGS to the data dog label based on the JSON environment CUSTOM_TAGS
    for key in CUSTOM_TAGS:
        custom_tags = ','.join([custom_tags, '{}:{}'.format(key, CUSTOM_TAGS[key])])

    METADATA[DD_CUSTOM_TAGS] = custom_tags

    try:
        # Route to the corresponding parser
        event_type = parse_event_type(event)

        if event_type == 's3':
            logs = s3_handler(s, event)

        elif event_type == 'awslogs':
            logs = awslogs_handler(s, event)

        for log in logs:
            send_entry(s, log)

    except Exception as exception:
        # Logs through the socket the error
        err_message = 'Error parsing the object. Exception: {}'.format(str(exception))
        send_entry(sock, err_message)
        raise exception
    finally:
        sock.close()


# Utility functions

def parse_event_type(event):
    if event.get('Records'):
        if 's3' in event['Records'][0]:
            return 's3'

    elif 'awslogs' in event:
        return 'awslogs'

    raise Exception('Event type not supported (see #Event supported section)')


# Handle S3 events
def s3_handler(s, event):
    s3 = boto3.client('s3')

    # Get the object from the event and show its content type
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.unquote_plus(event['Records'][0]['s3']['object']['key']).decode('utf8')

    METADATA[DD_SOURCE] = parse_event_source(event, key)

    # Extract the S3 object
    response = s3.get_object(Bucket=bucket, Key=key)
    body = response['Body']
    data = body.read()

    structured_logs = []

    # If the name has a .gz extension, then decompress the data
    if key[-3:] == '.gz':
        data = zlib.decompress(data, 16 + zlib.MAX_WBITS)

    if is_cloudtrail(str(key)):
        cloud_trail = json.loads(data)
        for event in cloud_trail['Records']:
            # Create structured object and send it
            structured_line = merge_dicts(event, {'aws': {'s3': {'bucket': bucket, 'key': key}}})
            structured_logs.append(structured_line)
    else:
        # Send lines to Datadog
        for line in data.splitlines():
            # Create structured object and send it
            structured_line = {'aws': {'s3': {'bucket': bucket, 'key': key}}, 'message': line}
            structured_logs.append(structured_line)

    return structured_logs


# Handle CloudWatch events and logs
def awslogs_handler(s, event):
    # Get logs
    data = zlib.decompress(base64.b64decode(event['awslogs']['data']), 16 + zlib.MAX_WBITS)
    logs = json.loads(str(data))
    #Set the source on the logs
    source = logs.get('logGroup', 'cloudwatch')
    METADATA[DD_SOURCE] = parse_event_source(event, source)

    structured_logs = []

    # Send lines to Datadog
    for log in logs['logEvents']:
        # Create structured object and send it
        structured_line = merge_dicts(log, {
            'aws': {
                'awslogs': {
                    'logGroup': logs['logGroup'],
                    'logStream': logs['logStream'],
                    'owner': logs['owner']
                }
            }
        })
        structured_logs.append(structured_line)

    return structured_logs


def send_entry(s, log_entry):
    # The log_entry can only be a string or a dict
    if isinstance(log_entry, str):
        log_entry = {'message': log_entry}
    elif not isinstance(log_entry, dict):
        raise Exception(
            'Cannot send the entry as it must be either a string or a dict. Provided entry: '
            + str(log_entry)
        )

    # Merge with METADATA
    log_entry = merge_dicts(log_entry, METADATA)

    # Send to Datadog
    str_entry = json.dumps(log_entry)
    print(str_entry)
    prefix = '%s ' % DD_API_KEY
    return s.send((prefix + str_entry + '\n').encode('UTF-8'))


def merge_dicts(a, b, path=None):
    if path is None:
        path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_dicts(a[key], b[key], path + [str(key)])
            elif a[key] == b[key]:
                pass  # same leaf value
            else:
                raise Exception(
                    'Conflict while merging METADATAs and the log entry at %s' % '.'.join(path + [str(key)])
                )
        else:
            a[key] = b[key]
    return a


def is_cloudtrail(key):
    match = CT_REGEX.search(key)
    return bool(match)


def parse_event_source(event, key):
    if 'lambda' in key:
        return 'lambda'
    if is_cloudtrail(str(key)):
        return 'cloudtrail'
    if 'elasticloadbalancing' in key:
        return 'elb'
    if 'redshift' in key:
        return 'redshift'
    if 'cloudfront' in key:
        return 'cloudfront'
    if 'kinesis' in key:
        return 'kinesis'
    if 'awslog' in event:
        return 'cloudwatch'
    if 'Records' in event and len(event['Records']) > 0:
        if 's3' in event['Records'][0]:
            return 's3'
    return 'aws'
