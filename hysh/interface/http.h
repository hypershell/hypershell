
#pragma once

#include "hysh/interface/stream.h"

hy_declare_interface(hy_http_request_line);
hy_declare_interface(hy_http_response_line);
hy_declare_interface(hy_http_handler);
hy_declare_interface(hy_http_handler_listener);
hy_declare_interface(hy_http_response_writer);

hy_define_interface(hy_http_request_line, hy_object)
    hy_error (*http_version)(void *self, hy_string *retval);
    
    hy_error (*request_method)(void *self, hy_string *retval);
    
    hy_error (*request_path)(void *self, hy_string *retval);
    
    hy_error (*query_string)(void *self, hy_string *retval);
hy_end

hy_define_interface(hy_http_response_line, hy_object)
    hy_error (*http_version)(void *self, hy_string *retval);
    
    hy_error (*status_code)(void *self, uint32_t *retval);
    
    hy_error (*status_message)(void *self, hy_string *retval);
hy_end

hy_define_interface(hy_http_handler, hy_object)
    hy_error (*handle_http_request)(void *self, 
        hy_http_request_line request_line,
        hy_table request_headers,
        hy_read_stream request_body,
        hy_http_response_writer response_writer);
hy_end

hy_define_interface(hy_http_handler_listener, hy_object)    
    hy_error (*on_http_response_available)(void *self,
        hy_http_response_line response_line,
        hy_table request_headers,
        hy_read_stream response_stream);
hy_end

hy_define_interface(hy_http_response_writer, hy_object)    
    hy_error (*write_response)(void *self,
        hy_http_response_line response_line,
        hy_table request_headers,
        hy_read_stream response_stream);
hy_end