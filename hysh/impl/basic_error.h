
#pragma once

#include "hysh/interface/object.h"

#define hy_define_basic_error(error_name, error_code, error_message) \
    hy_basic_error error_name = { \
        { \
            &error_name, \
            &hy_basic_error_methods, \
        }, \
        error_code, \
        error_message \
    };

#define hy_declare_basic_error(error_name) \
    hy_basic_error error_name

typedef struct hy_basic_error {
    hy_error interface;
    
    uint64_t error_code;
    
    const char *error_message;
} hy_basic_error;

hy_error hy_basic_error_add_ref(void *self);

hy_error hy_basic_error_de_ref(void *self);

hy_error hy_basic_error_query_interface(void *_self, hy_iid iid, hy_object *retval);

hy_error hy_basic_error_error_code(void *_self, uint64_t *retval);

hy_error hy_basic_error_error_message(void *_self, const char **retval);

hy_error_methods hy_basic_error_methods;

hy_declare_basic_error(hy_fatal_error);
hy_declare_basic_error(hy_failure);
hy_declare_basic_error(hy_query_interface_failure);
hy_declare_basic_error(hy_not_found);
hy_declare_basic_error(hy_not_implemented);