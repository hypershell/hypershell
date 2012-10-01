
#pragma once

extern "C" {
#include "hysh/interface/object.h"
#include "hysh/impl/basic_error.h"
}

namespace hysh {

class Object
{
  public:
    static const hy_iid InterfaceId = hy_object_iid;

    Object() :
        mPtr(0)
    { }

    Object(hy_object *ptr) :
        mPtr(ptr)
    { }

    template <typename Interface>
    hy_error* QueryInterface(Interface **retval) {
        hy_object *newobj;
        hy_iid iid = Interface::InterfaceID;

        hy_error *err = Methods()->query_interface(Self(), iid, &newobj);
        
        if(!err) *retval = static_cast<Interface*>(newobj);
        
        return err;
    }

    hy_error* ClassId(hy_cid *retval) {
        return Methods()->class_id(Self(), retval);
    }
    
    hy_error* AddRef() {
        return Methods()->add_ref(Self());
    }
    
    hy_error* DeRef() {
        return Methods()->de_ref(Self());
    }

    void* Self() {
        return Ptr()->self;
    }

    hy_object* Ptr() {
        return mPtr;
    }

    hy_object** EditPtr() {
        return &mPtr;
    }
    
    hy_object_methods* Methods() {
        return Ptr()->methods;
    }

    bool IsNull() {
        return mPtr == 0;
    }

    operator hy_object*() {
        return Ptr();
    }

    operator bool() {
        return !IsNull();
    }

  private:
    hy_object *mPtr;
};

} // namespace