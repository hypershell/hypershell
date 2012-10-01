
#pragma once

#include "hysh/interface/object.h"

#define hy_define_basic_error(error_name, error_code, error_message) \
    hy_basic_error error_name; \
    static const hy_basic_error error_name = { \
        { \
            &error_name, \
            &hy_basic_error_methods, \
        }, \
        error_code, \
        error_message \
    };

typedef struct hy_basic_error {
    hy_error interface;
    hy_error_code error_code;;
    const char *error_message;
} hy_basic_error;

static const hy_cid hy_basic_error_cid = 0;

hy_error* hy_basic_error_add_ref(void *self);

hy_error* hy_basic_error_de_ref(void *self);

hy_error* hy_basic_error_class_id(void *self, hy_cid *retval);

hy_error* hy_basic_error_query_interface(void *_self, hy_iid iid, hy_object **retval);

hy_error* hy_basic_error_error_code(void *_self, uint64_t *retval);

hy_error* hy_basic_error_error_message(void *_self, const char **retval);

static hy_error_methods hy_basic_error_methods = {
    {
        hy_basic_error_add_ref,
        hy_basic_error_de_ref,
        hy_basic_error_class_id,
        hy_basic_error_query_interface
    },
    hy_basic_error_error_code,
    hy_basic_error_error_message
};