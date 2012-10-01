
#pragma once

hy_declare_interface(hy_list);

hy_define_interface(hy_list, hy_object)    
    hy_error* (*value)(void *self, hy_object *retval, hy_iid *ret_iid);
    
    hy_error* (*typed_value)(void *self, hy_iid iid, hy_object **retval);
    
    hy_error* (*size)(void *self, uint64_t *retval);
    
    hy_error* (*has_next)(void *self, bool *retval);
    
    hy_error* (*next)(void *self, hy_list **retval);
hy_end