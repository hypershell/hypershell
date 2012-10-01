
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "hysh/interface/error.h"

#define hy_cast(target, interface, object) \
    (target).self = (object).self; \
    (target).methods = (interface##_methods*) (object).methods

#define hy_define_interface(name, parent_name) \
    struct name##_methods; \
    struct name { \
        void *self; \
        \
        struct name##_methods *methods; \
    }; \
    struct name##_methods { \
        struct parent_name##_methods parent; 

#define hy_end };

#define hy_declare_interface(name) \
    typedef struct name name;

typedef uint64_t hy_iid;
typedef uint64_t hy_cid;
typedef uint64_t hy_error_code;

static const hy_iid hy_error_iid = 0x4ff19839fd3c889f;
static const hy_iid hy_object_iid = 0;

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
    hy_error* (*add_ref)(void *self);
    
    hy_error* (*de_ref)(void *self);
    
    hy_error* (*class_id)(void *self, hy_cid *retval);
    
    hy_error* (*query_interface)(void *self, hy_iid iid, hy_object **retval);
    
} hy_object_methods;

typedef struct hy_error_methods {
    hy_object_methods parent;
    
    hy_error* (*error_code)(void *self, hy_error_code *retval);
    
    hy_error* (*error_message)(void *self, const char **retval);
    
} hy_error_methods;