
#pragma once

struct hy_list_methods;
struct hy_typed_list_methods;

typedef struct hy_list {
    void *self;
    
    struct hy_list_methods *methods;
} hy_list;

typedef struct hy_typed_list {
    void *self;
    
    struct hy_typed_list_methods *methods;
    
} hy_typed_list;

typedef struct hy_list_methods {
    hy_object_methods parent;
    
    hy_error (*value)(void *self, hy_object *retval);
    
    hy_error (*has_next)(void *self, bool *retval);
    
    hy_error (*next)(void *self, hy_list *retval);
    
} hy_list_methods;

typedef struct hy_typed_list_methods {
    hy_list_methods parent;
    
    hy_error (*list_type)(void *self, hy_iid *retval);
    
    
    hy_error (*next)(void *self, hy_typed_list *retval);
    
} hy_typed_list_methods;