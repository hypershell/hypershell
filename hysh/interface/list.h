
#pragma once

hy_declare_interface(hy_list);
hy_declare_interface(hy_typed_list);

hy_define_interface(hy_list, hy_object)    
    hy_error (*value)(void *self, hy_object *retval);
    
    hy_error (*has_next)(void *self, bool *retval);
    
    hy_error (*next)(void *self, hy_list *retval);
hy_end_define

hy_define_interface(hy_typed_list, hy_list)
    hy_error (*list_type)(void *self, hy_iid *retval);
    
    hy_error (*next)(void *self, hy_typed_list *retval);
hy_end_define