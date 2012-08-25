
#pragma once

#include "hysh/interface/data_buffer.h"

struct hy_string_methods;

typedef struct hy_string {
    void *self;
    
    struct hy_string_methods *methods;
    
} hy_string;

typedef struct hy_string_methods {
    hy_data_buffer_methods parent;
    
    hy_error (*length)(void *self, uint64_t *retval);
    
    hy_error (*c_string)(void *self, const char **retval);
    
} hy_string_methods;