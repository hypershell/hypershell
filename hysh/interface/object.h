
#pragma once

#include <stdint.h>
#include "hysh/interface/error.h"

#define hy_cast(target, interface, object) \
    (target).self = (object).self; \
    (target).methods = (interface##_methods*) (object).methods

#define hy_define_interface(name, parent_name) \
    typedef struct name##_methods name##_methods; \
    typedef struct name { \
        void *self; \
        \
        name##_methods *methods; \
    } name; \
    struct name##_methods { \
        parent_name##_methods parent; 

#define hy_end_define };

typedef uint64_t hy_iid;

struct hy_object_methods;
struct hy_error_methods;

typedef struct hy_object {
    void *self;
    
    struct hy_object_methods *methods;
    
} hy_object;

typedef struct hy_error {
    void *self;
    
    struct hy_error_methods *methods;
    
} hy_error;

typedef struct hy_object_methods {
    hy_error (*add_ref)(void *self);
    
    hy_error (*de_ref)(void *self);
    
    hy_error (*query_interface)(void *self, hy_iid iid, hy_object *retval);
    
} hy_object_methods;

typedef struct hy_error_methods {
    hy_object_methods parent;
    
    hy_error (*error_code)(void *self, hy_error_code *retval);
    
    hy_error (*error_message)(void *self, const char **retval);
    
} hy_error_methods;

static hy_error hy_success = { 0, 0 };
static const hy_object hy_null = { 0, 0 };