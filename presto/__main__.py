from __future__ import print_function, with_statement

import sys
import os
import io
import stat
import hashlib

import markdown
import mdx_grid_tables as grid_tables
import six

import presto.output as output
import presto.convert as convert
import presto.config as config
import presto.options as options


def get_cache(cache_file):
    cache = {}

    if not os.path.isfile(cache_file):
        return cache

    with io.open(cache_file, mode='r') as f:
        for line in f:
            tokens = line.split()
            cache[tokens[0]] = tokens[1]

    return cache


def write_cache(cache, cache_file):
    with io.open(cache_file, mode='w') as f:
        for (k, v) in cache.items():
            f.write(six.u("{}\t{}\n".format(k, v)))


def compute_hash(file):
    h = hashlib.md5()

    for line in file:
        h.update(line.encode('utf-8'))

    file.seek(0)
    return h.hexdigest()


def makedirs(dirpath):
    """Recursively create directories up to the leaf directory, if they
    do not already exist. Unlike os.makedirs, this function will not
    produce an error if any intermediate directories exist or have modes
    not matching the 'mode' parameter.
    """

    head, tail = os.path.split(dirpath)
    if tail == '':
        # root directory or current relative root has been reached
        return
    else:
        # make all parent directories
        makedirs(head)

        # make this directory if it does not exist
        if not os.path.isdir(dirpath):
            os.mkdir(dirpath)
            print("created directory '{}'".format(dirpath))


def should_publish(path):
    filename = os.path.basename(path)
    return filename[0] != '_'


def copy_htaccess(path, output_dir):
    old_umask = os.umask(0o002)

    parts = path.split(os.sep)
    parts[-1] = '.htaccess'
    output_path = output_dir + os.sep + os.sep.join(parts[1:])

    try:
        makedirs(os.path.dirname(output_path))
    except:
        if options.get('debug'):
            output.traceback()

        output.error("cannot make directories for htaccess '{}'".format(output_path))
        os.umask(old_umask)
        return False

    was_here = os.path.isfile(output_path)

    try:
        with io.open(path, mode='r') as htfile:
            with io.open(output_path, mode='w') as of:
                of.write(htfile.read())
    except:
        if options.get('debug'):
            output.traceback()

        output.error("could not create htaccess '{}'".format(output_path))
        os.umask(old_umask)
        return False

    if not was_here:
        st = os.stat(output_path)
        os.chmod(output_path, st.st_mode | stat.S_IXGRP | stat.S_IXOTH)

    os.umask(old_umask)
    print("created htaccess file '{}'".format(output_path))
    return True


def config_get(name):
    value = config.get(name)
    if value is None:
        output.error('no value for required configuration variable "{}"'.format(name))
        sys.exit(1)
    else:
        return value


# post-process the whitelist: split on commas and remove extra spaces
whitelist = [s.strip() for s in config_get('whitelist').split(',')]

os.umask(0o002)

extensions = [
    'def_list',
    'footnotes',
    'meta',
    'smarty',
    'headerid',
    'tables',
    'codehilite',
    'admonition',
    'toc',
    grid_tables.GridTableExtension()
]

args = {
    'extensions': extensions,
    'extension_configs': {'smarty': [('smart_ellipses', False)]},
    'output_format': 'html5',
    'lazy_ol': False
}

md = markdown.Markdown(**args)

with io.open(config_get('template_file'), 'r') as f:
    template = f.read()

num_published, num_errors, num_skipped, num_removed = 0, 0, 0, 0

try:
    cache = get_cache(config_get('cache_file'))
except:
    if options.get('debug'):
        output.traceback()

    output.error('could not open cache file')
    cache = {}
    num_errors += 1

expected_files = []

for dirpath, dirnames, filenames in os.walk(config_get('markdown_dir')):
    for f in filenames:
        if '.markdown' not in f and f != 'htaccess':
            continue

        if f[0] in ['.', '#']:
            continue

        if f[-1] == '~':
            continue

        path = os.path.join(dirpath, f)
        relpath = os.path.relpath(path, config_get('markdown_dir'))

        if should_publish(path):
            expected_files.append(relpath)
        else:
            if relpath in cache:
                del cache[relpath]

        try:
            infile = io.open(path, mode='r', encoding='utf-8')
        except:
            if options.get('debug'):
                output.traceback()

            output.error("unable to read '{}'".format(relpath))
            num_errors += 1
            continue

        hash = compute_hash(infile)

        if relpath not in cache or cache[relpath] != hash:
            if should_publish(path):
                cache[relpath] = hash
            else:
                output.skipped(relpath)
                num_skipped += 1
                continue
        else:
            # no change to this file since last time
            continue

        if f == 'htaccess':
            if not copy_htaccess(path, config_get('output_dir')):
                num_errors += 1
            else:
                num_published += 1
            continue

        html, errors = convert.md_to_html(md, template, infile)

        if errors:
            for err in errors:
                output.error('{}: {}'.format(relpath, err))
                num_errors += 1

            if not options.get('use_empty'):
                # invalidate cache so this file is not skipped next time
                if relpath in cache:
                    del cache[relpath]

                num_skipped += 1
                continue

        # create path to output directory
        output_path = os.path.join(
            config_get('output_dir'),
            relpath.replace('.markdown', '.html')
        )

        try:
            makedirs(os.path.dirname(output_path))
        except:
            if options.get('debug'):
                output.traceback()

            output.error("cannot make directories for '{}'".format(relpath))
            cache[relpath] = 'dirs-failed'
            num_errors += 1
            continue

        try:
            with io.open(output_path, mode='w') as of:
                of.write(html)
        except:
            if options.get('debug'):
                output.traceback()

            output.error("cannot write output file '{}'".format(relpath))
            cache[relpath] = 'write-failed'
            num_errors += 1
            continue

        num_published += 1
        output.published(relpath)

        md.reset()

# done publishing all HTML
# now remove the HTML files for non-existent Markdown
for dirpath, dirnames, filenames in os.walk(config_get('output_dir')):

    # skip any directories specified in the whitelist
    while True:
        for d in dirnames:
            dirname = os.path.join(dirpath, d)
            reldirname = os.path.relpath(dirname, config_get('output_dir'))

            if reldirname in whitelist:
                dirnames.remove(d)
                break
        else:
            break

    for f in filenames:
        if f[0] == '.' and f != '.htaccess':
            continue

        path = os.path.join(dirpath, f)
        relpath = os.path.relpath(path, config_get('output_dir'))

        head, tail = os.path.split(relpath)
        if f == '.htaccess':
            relpath = os.path.join(head, 'htaccess')
        else:
            relpath = os.path.join(head, f.replace('.html', '.markdown'))

        if relpath not in expected_files:
            if relpath in cache:
                del cache[relpath]

            try:
                os.remove(path)
                num_removed += 1
                output.removed(relpath)
            except:
                if options.get('debug'):
                    output.traceback()

                output.error("unable to remove '{}'".format(relpath))
                num_errors += 1

# make one more pass to remove any directories that are now empty
for dirpath, dirnames, filenames in os.walk(config_get('output_dir')):

    # skip any directories specified in the whitelist
    while True:
        for d in dirnames:
            dirname = os.path.join(dirpath, d)
            reldirname = os.path.relpath(dirname, config_get('output_dir'))

            if reldirname in whitelist:
                dirnames.remove(d)
                break
        else:
            break

    if len(filenames) == 0 and len(dirnames) == 0:
        try:
            os.rmdir(dirpath)
            output.removed('empty directory ' + dirpath)
        except:
            if options.get('debug'):
                output.traceback()

            output.error("unable to remove directory '{}'".format(dirpath))

try:
    write_cache(cache, config_get('cache_file'))
except:
    if options.get('debug'):
        output.traceback()

    output.error('could not write cache file')
    num_errors += 1

print('{} files published, {} files skipped, {} files removed; {} errors'.format(
    num_published, num_skipped, num_removed, num_errors
))
