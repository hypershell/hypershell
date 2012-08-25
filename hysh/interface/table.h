
#pragma once

#include "hysh/interface/string.h"
#include "hysh/interface/list.h"

struct hy_table_methods;

typedef typedef hy_table {
    void *self;
    
    struct hy_table_methods *methods;
    
} hy_table;

typedef struct hy_table_methods {
    hy_object_methods parent;
    
    hy_error (*get_value)(void *self, hy_string key, hy_string *retval);
    
    hy_error (*get_keys)(void *self, hy_typed_list *retval);
    
} hy_table_methods;