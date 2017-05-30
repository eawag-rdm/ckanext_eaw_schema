import ckan.plugins as plugins
import ckantoolkit as toolkit
from ckan.plugins.interfaces import IPackageController
from ckanext.eaw_vocabularies.validate_solr_daterange import SolrDaterange
from ckanext.scheming.validation import scheming_validator
import pylons.config as config
from itertools import count
import json
import logging
import re

missing = toolkit.missing
_ = toolkit._
logger = logging.getLogger(__name__)

def _json2list(value):
    if isinstance(value, list):
        val = value
    elif isinstance(value, basestring):
        val = [val.strip() for val in value.split(sep) if val.strip()]
    else:
        raise toolkit.Invalid("Only strings or lists allowed")
    val = json.dumps(val)

def _everything2stringlist(value):
    val_out = []
    if isinstance(value, list):
        for v in value:
            if isinstance(v, basestring):
                val_out.append(v)
            else:
                val_out.append(repr(v))
        return(val_out)
    val_out.append(str(value))
    return val_out


def vali_daterange(values):
    '''
    Initial conversion, 2 possibilities:
    1) <values> is a json representation of a list of DateRange-strings
       (output of validator repeating_text),
    2) <values> is one DateRange-string (output from ordinary text field)

    Both are initially converted to a list of DateRange strings
    
    Then add some slack to proper format of timerange before submitting
    to real validator:
      + insert trailing "Z" for points in time if necessary
      + add brackets if necessary
    '''
    def _fix_timestamp(ts):
        try:
            fixed = (ts + "Z" if len(ts.split(":")) == 3 and ts[-1] != "Z"
                     else ts)
        except:
            pass
        return fixed
    def _to_list_of_strings(valuestring):
        try:
            values = json.loads(valuestring)
        except ValueError:
            values = [valuestring]
        return(values)

    values = _to_list_of_strings(values)
    valid_values = []
    for value in values:
        value = value.strip()
        try:
            timestamps = [x.strip('[]') for x in value.split(" TO ")]
        except ValueError:
            pass
        else:
            timestamps = [_fix_timestamp(ts) for ts in timestamps]
            value = " TO ".join(timestamps)
            value = "[" + value + "]" if len(timestamps) == 2 else value 
        valid_values.append(SolrDaterange.validate(value))
    valid_values = json.dumps(valid_values)
    return valid_values

def output_daterange(values):
    '''
    For display:
      + remove brackets from timerange.
      + remove trailing "Z"  from time-points.
    '''
    def _fix_timestamp(ts):
        return(ts[0:-1] if len(ts.split(":")) == 3 and ts[-1] == "Z" else ts)

    # We try to output everything, even "illegal" values.
    values = _everything2stringlist(values)
    values_out = []
    for value in values:
        value = value.strip("[]")
        try:
            timestamps = value.split(" TO ")
        except ValueError:
                pass
        else:
            value = " TO ".join([_fix_timestamp(ts) for ts in timestamps])
        values_out.append(value)
    return(values_out)

def eaw_schema_multiple_string_convert(typ):
    '''
    Converts a string that represents multiple strings according to
    certain conventions to a json-list for storage. The convention has to 
    be given as parameter for this validator in the schema file
    (e.g. "schema_default.json")
    Currently implemented: typ = pipe ("|" as separator),
                           typ = textbox ("\r\n" as separator)
    '''

    def validator(value):
        sep = {"pipe": "|", "textbox": "\r\n"}[typ]
        if isinstance(value, list):
            val = value
        elif isinstance(value, basestring):
            val = [val.strip() for val in value.split(sep) if val.strip()]
        else:
            raise toolkit.Invalid("Only strings or lists allowed")
        val = json.dumps(val)
        return val
    
    return validator

## If value is a string, and the string can be parsed as json,
## validate only if the encoded value is not empty or None
## Use for json-encoded "repeating"-fields, **after** "repeating_text".
def eaw_schema_json_not_empty(key, data, errors, context):
    if errors[key]:
        return
    value = data.get(key)
    try:
        val = json.loads(value)
    except (TypeError, ValueError):
        logger.warn("Can't parse {} ({}) as json -- this should not happen"
                    .format(value, type(value)))
        return
    if not val and val != 0:
        errors[key].append(_('Missing value'))
        raise toolkit.StopOnError


## Maybe this could be replaced / merged with repeating_text_output.
def eaw_schema_multiple_string_output(value):
    try:
        value = json.loads(value)
    except ValueError:
        raise toolkit.Invalid("String doesn't parse into JSON")
    return value


@scheming_validator
def eaw_schema_multiple_choice(field, schema):
    """
    Accept zero or more values from a list of choices and convert
    to a json list for storage:

    1. a list of strings, eg.:

       ["choice-a", "choice-b"]

    2. a single string for single item selection in form submissions:

       "choice-a"

    #######################################################################
    HvW - 2016-06-21
    This was copied from ckanext-scheming.validation.
    Amendement:
        A value of <empty string> is ignored (no "unexpected choice"-error).
    We use this to pass (via hidden field, in eaw_multiple_select_js.html)
    an empty string, even if no field is selected, so that the field is
    included in the form data and not populated from the database
    by package.edit().
    #######################################################################
    """
    choice_values = set(c['value'] for c in field['choices'])

    def validator(key, data, errors, context):
        # if there was an error before calling our validator
        # don't bother with our validation
        if errors[key]:
            return

        value = data[key]
        if value is not missing:
            if isinstance(value, basestring):
                value = [value]
            elif not isinstance(value, list):
                errors[key].append(_('expecting list of strings'))
                return
        else:
            value = []

        selected = set()
        for element in value:
            if element in choice_values:
                selected.add(element)
                continue
            if element == '':
                continue
            errors[key].append(_('unexpected choice "%s"') % element)

        if not errors[key]:
            data[key] = json.dumps([
                c['value'] for c in field['choices'] if c['value'] in selected])

            if field.get('required') and not selected:
                errors[key].append(_('Select at least one'))

    return validator


## Template helper functions

def eaw_schema_set_default(values, default_value):
    ## Only set default value if current value is empty string or None
    ## or a list containing only '' or None.
    if isinstance(values, basestring) or values is None:
        if values not in ['', None]:
            return values
        islist = False
    elif isinstance(values, list):
        if not all([x in ['', None] for x in values]):
            return values
        islist = True
    else:
        return values

    # special default value resulting in "Full Name <email>"
    if default_value == "context_fullname_email":
        val = u'{} <{}>'.format(toolkit.c.userobj.fullname,
                               toolkit.c.userobj.email)

    ## insert elif clauses for other defaults

    else:
        val = default_value

    # deal with list/string - duality
    if islist:
        values[0] = val
    else:
        values = val

    return values

def eaw_schema_get_values(field_name, form_blanks, data):
    '''
    Get data from repeating_text-field from either field_name (if the
    field comes from the database) or construct from several field-N -
    entries in case data wasn't saved yet, i.e. a validation error occurred.
    In the first case, show additional <form_blanks> empty fields. In the
    latter, don't change the form.

    '''

    fields = [re.match(field_name + "-\d+", key) for key in data.keys()]
    if all(f is None for f in fields):
        # not coming from form submit -> get value from DB
        value = data.get(field_name)
        value = value if isinstance(value, list) else [value]
        value = value + [''] * max(form_blanks -len(value), 1)
    else:
        # using form data
        fields = sorted([r.string for r in fields if r])
        value = [data[f] for f in fields if data[f]]
        value = value + [''] * max(form_blanks - len(value), 0)
    return value


class Eaw_SchemaPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IValidators)
    plugins.implements(plugins.IPackageController, inherit=True)
    plugins.implements(plugins.ITemplateHelpers)

    ## The fields that should be indexed as list
    ## a cludge until I figure out how to do that DRY.
    json2list_fields = [
        'substances',
        'variables',
        'systems',
        'timerange',
        'taxa',
        'geographic_name',
    ]

    # IConfigurer
    def update_config(self, config_):
        toolkit.add_template_directory(config_, 'templates')
        # toolkit.add_public_directory(config_, 'public')
        toolkit.add_resource('fanstatic', 'eaw_schema')
        
    # IValidators
    def get_validators(self):
        return {"vali_daterange": vali_daterange,
                "output_daterange": output_daterange,
                "eaw_schema_multiple_string_convert":
                    eaw_schema_multiple_string_convert,
                "eaw_schema_multiple_string_output":
                    eaw_schema_multiple_string_output,
                "eaw_schema_multiple_choice":
                    eaw_schema_multiple_choice,
                "eaw_schema_json_not_empty":
                    eaw_schema_json_not_empty
        }

    # IPackageController
    
    # Parse a list of DateRange strings out of a JSON-string.
    # We try to recover from some cases of illegal values to
    # minimize crashes of the indexer.
    # Illegal values can occur when the schema has changed. 
    def before_index(self, pkg_dict):
        for field in self.json2list_fields:
            val = pkg_dict.get(field)
            if not val or isinstance(val, list):
                continue
            try:
                valnew = json.loads(val)
            except:
                logger.debug("{} doesn't parse as JSON".format(repr(val)))
                val1 = json.dumps([repr(val)])
                logger.debug("replacing with {}".format(repr(val1)))
                valnew = json.loads(val1)
            if isinstance(valnew, list):
                pkg_dict[field] = valnew
            else:
                valnew_l = _everything2stringlist(valnew)
                logger.debug("{} is not a list, try replacing with {}"
                       .format(valnew, valnew_l))
                pkg_dict[field] = valnew_l
        return(pkg_dict)

    # ITemplateHelpers
    def get_helpers(self):
        return {'eaw_schema_set_default': eaw_schema_set_default,
                'eaw_schema_get_values': eaw_schema_get_values}
    
 
